import functools
import hashlib
import logging
import tempfile
import time

import dacite
import github3.exceptions

import ccc.github
import ctx
import checkmarx.client
import checkmarx.model
import product.model
import checkmarx.util
import version


ctx.configure_default_logging()
logger = logging.getLogger(__name__)


class RefGuessingFailedError(Exception):
    pass


@functools.lru_cache
def component_logger(component):
    return logging.getLogger(component.name())


def upload_and_scan_repo(
        component: product.model.Component,  # needs to remain at first position (currying)
        checkmarx_client: checkmarx.client.CheckmarxClient,
        team_id: str,
):
    cx_project = _create_checkmarx_project(
        checkmarx_client=checkmarx_client,
        team_id=team_id,
        component_name=component.name(),
    )
    try:
        commit_hash = _guess_commit_from_ref(component=component)
    except github3.exceptions.NotFoundError as e:
        raise RefGuessingFailedError(e)

    project = dacite.from_dict(
        checkmarx.model.ProjectDetails,
        cx_project.client.get_project_by_id(cx_project.project_id).json()
    )

    clogger = component_logger(component=component)

    last_scans = cx_project.client.get_last_scans_of_project(cx_project.project_id)

    if len(last_scans) < 1:
        clogger.info('No scans found in project history')
        with tempfile.TemporaryFile() as tmp_file:
            clogger.info('downloading sources for component.')
            cx_project.download_zipped_repo(
                tmp_file=tmp_file,
                ref=commit_hash,
            )
            clogger.info('uploading sources for component')
            cx_project.upload_zip(file=tmp_file)

        project.set_custom_field(checkmarx.model.CustomFieldKeys.HASH, commit_hash)
        project.set_custom_field(checkmarx.model.CustomFieldKeys.COMPONENT_NAME, component.name())

        cx_project.client.update_project(project)
        scan_id = cx_project.start_scan()
        clogger.info(f'created scan with id {scan_id}')

        return cx_project.poll_and_retrieve_scan(
            scan_id=scan_id,
            component=component,
        )

    last_scan = last_scans[0]
    scan_id = last_scan.id

    if checkmarx.util.is_scan_finished(last_scan):
        clogger.info('No active scan found for component. Checking for hash')

        if checkmarx.util.is_scan_necessary(project=project, hash=commit_hash):
            clogger.info('downloading repo')
            with tempfile.TemporaryFile() as tmp_file:
                cx_project.download_zipped_repo(
                    tmp_file=tmp_file,
                    ref=commit_hash,
                )
                clogger.info('uploading sources')
                cx_project.upload_zip(file=tmp_file)

            project.set_custom_field(
                checkmarx.model.CustomFieldKeys.HASH,
                commit_hash,
            )
            project.set_custom_field(
                checkmarx.model.CustomFieldKeys.COMPONENT_NAME,
                component.name(),
            )
            cx_project.client.update_project(project)

            scan_id = cx_project.start_scan()
            clogger.info(f'created scan with id {scan_id}')
    else:
        clogger.info(f'scan with id: {last_scan.id} for component {component.name()} '
              'already running. Polling last scan.'
        )

    return cx_project.poll_and_retrieve_scan(
        scan_id=scan_id,
        component=component,
    )


def _guess_commit_from_ref(component: product.model.Component):
    """
    heuristically guess the appropriate git-ref for the given component's version
    """
    github_api = _github_api(component_name=component)
    github_repo = github_api.repository(
        component.github_organisation(),
        component.github_repo(),
    )

    clogger = component_logger(component=component)

    def in_repo(commit_ish):
        clogger.info(f"commit-ish {commit_ish}")
        try:
            return github_repo.ref(commit_ish).object.sha
        except github3.exceptions.NotFoundError:
            pass

        try:
            return github_repo.commit(commit_ish).sha
        except (github3.exceptions.UnprocessableEntity, github3.exceptions.NotFoundError):
            return None

    # first guess: component version could already be a valid "Gardener-relaxed-semver"
    version_str = str(version.parse_to_semver(component))
    commit = in_repo(version_str)
    if commit:
        return commit
    # also try unmodified version-str
    if (commit := in_repo(component.version())):
        return commit

    # second guess: split commit-hash after last `-` character (inject-commit-hash semantics)
    if '-' in (version_str := str(component.version())):
        last_part = version_str.split('-')[-1]
        commit = in_repo(last_part)
        if commit:
            return commit

    # third guess: branch
    try:
        return github_repo.branch(version_str).commit.sha
    except github3.exceptions.NotFoundError:
        pass

    # still unknown commit-ish throw error
    raise RefGuessingFailedError(
        f'failed to guess on ref for {component.name()=}{component.version()=}'
    )


def _github_api(component_name: product.model.ComponentName):
    github_cfg = ccc.github.github_cfg_for_hostname(host_name=component_name.github_host())
    github_api = ccc.github.github_api(github_cfg=github_cfg)
    return github_api


def _create_checkmarx_project(
        checkmarx_client: checkmarx.client.CheckmarxClient,
        team_id: str,
        component_name: str
):
    if isinstance(component_name, str):
        component_name = product.model.ComponentName.from_github_repo_url(component_name)
    elif isinstance(component_name, product.model.ComponentName):
        component_name = component_name
    else:
        raise NotImplementedError

    github_api = _github_api(component_name=component_name)

    project_name = _calc_project_name_for_component(component_name=component_name)

    project_id = _create_or_get_project(client=checkmarx_client, name=project_name, team_id=team_id)

    return CheckmarxProject(
        checkmarx_client=checkmarx_client,
        project_id=project_id,
        github_api=github_api,
        component_name=component_name,
    )


def _create_or_get_project(
        client: checkmarx.client.CheckmarxClient,
        name: str,
        team_id: str,
        is_public: bool = True
):
    try:
        project_id = client.get_project_id_by_name(project_name=name, team_id=team_id)
        return project_id
    except checkmarx.client.CXNotOkayException as e:
        if e.res.status_code == 404:
            return client.create_project(name, team_id, is_public).json().get('id')
        else:
            raise e


def _calc_project_name_for_component(component_name: product.model.ComponentName):
    return component_name.name().replace('/', '_')


class CheckmarxProject:
    def __init__(
            self,
            checkmarx_client: checkmarx.client.CheckmarxClient,
            project_id: str,
            github_api,
            component_name: product.model.ComponentName
    ):
        self.client = checkmarx_client
        self.project_id = int(project_id)
        self.component_name = component_name
        self.github_api = github_api

    def poll_and_retrieve_scan(self, scan_id: int, component: product.model.Component):
        scan_result = self._poll_scan(scan_id=scan_id, component=component)

        if scan_result.status_value() is not checkmarx.model.ScanStatusValues.FINISHED:
            logger.error(f'scan for {component.name()} failed with {scan_result.status=}')
            raise RuntimeError('Scan did not finish successfully')

        clogger = component_logger(component)
        clogger.info('retrieving scan statistics')
        statistics = self.scan_statistics(scan_id=scan_result.id)

        return checkmarx.model.ScanResult(
            component=component,
            scan_result=scan_result,
            scan_statistic=statistics,
        )

    def download_zipped_repo(self, tmp_file, ref: str):
        repo = self.github_api.repository(
            self.component_name.github_organisation(),
            self.component_name.github_repo()
        )

        url = repo._build_url('zipball', ref, base_url=repo._api)
        res = repo._get(url, verify=False, allow_redirects=True, stream=True)
        if not res.ok:
            raise RuntimeError(
                f'request to download github zip archive from {url=}'
                f' failed with {res.status_code=} {res.reason=}'
            )

        for chunk in res.iter_content(chunk_size=512):
            tmp_file.write(chunk)

        tmp_file.flush()
        tmp_file.seek(0)

    def upload_zip(self, file):
        self.client.upload_zipped_source_code(self.project_id, file)

    def update_project(self, project: checkmarx.model.ProjectDetails):
        self.client.update_project(project)

    def upload_source(self, ref: str):
        repo = self.github_api.repository(
            self.component_name.github_organisation(),
            self.component_name.github_repo()
        )

        url = repo._build_url('zipball', ref, base_url=repo._api)
        res = repo._get(url, verify=False, allow_redirects=True, stream=True)
        if not res.ok:
            raise RuntimeError(
                f'request to download github zip archive from {url=}'
                f' failed with {res.status_code=} {res.reason=}')

        sha1 = hashlib.sha1()

        with tempfile.TemporaryFile() as tmp_file:
            for chunk in res.iter_content(chunk_size=512):
                tmp_file.write(chunk)
                sha1.update(chunk)

            tmp_file.flush()
            tmp_file.seek(0)

            project = dacite.from_dict(
                checkmarx.model.ProjectDetails,
                self.client.get_project_by_id(self.project_id).json()
            )
            remote_hash = project.get_custom_field(checkmarx.model.CustomFieldKeys.HASH)

            current_hash = f'sha1:{sha1.hexdigest()}'
            if remote_hash and not remote_hash.startswith('sha1:'):
                raise NotImplementedError(remote_hash)

            if current_hash != remote_hash:
                logger.info(f'Uploading changes of repo {repo.name}')
                self.client.upload_zipped_source_code(self.project_id, tmp_file)
                project.set_custom_field(checkmarx.model.CustomFieldKeys.HASH, current_hash)
                project.set_custom_field(checkmarx.model.CustomFieldKeys.VERSION, ref)
                self.client.update_project(project)
                return True
            logger.info(f"given snapshot {ref} already scanned")
            return False

    def start_scan(self):
        scan_settings = checkmarx.model.ScanSettings(projectId=self.project_id)
        return self.client.start_scan(scan_settings)

    def _poll_scan(
            self,
            scan_id: int,
            component: product.model.Component,
            polling_interval_seconds=60
    ):
        def scan_finished():
            scan = self.client.get_scan_state(scan_id=scan_id)
            clogger = component_logger(component)
            clogger.info(f'polling for {scan_id=}. {scan.status.name=}')
            if checkmarx.util.is_scan_finished(scan):
                return scan
            return False

        result = scan_finished()
        while not result:
            # keep polling until result is ready
            time.sleep(polling_interval_seconds)
            result = scan_finished()
        return result

    def scan_statistics(self, scan_id: int):
        return self.client.get_scan_statistics(scan_id=scan_id)

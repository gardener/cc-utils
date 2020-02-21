import ccc.github
import hashlib
import dacite
import tempfile
import product.model
import checkmarx.client
import checkmarx.model
import ci.util


def create_project_facade(checkmarx_cfg_name: str, team_id: str, component_name: str):
    cfg_fac = ci.util.ctx().cfg_factory()
    client = checkmarx.client.CheckmarxClient(cfg_fac.checkmarx(checkmarx_cfg_name))

    if isinstance(component_name, str):
        component_name = product.model.ComponentName.from_github_repo_url(component_name)
    elif isinstance(component_name, product.model.ComponentName):
        component_name = component_name
    else:
        raise NotImplementedError

    github_cfg = ccc.github.github_cfg_for_hostname(host_name=component_name.github_host())
    github_api = ccc.github.github_api(github_cfg=github_cfg)

    project_name = _calc_project_name_for_component(component_name=component_name)

    project_id = _create_or_get_project(client=client, name=project_name, team_id=team_id)

    return CheckmarxProjectFacade(
        checkmarx_client=client,
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


class CheckmarxProjectFacade:
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

    def upload_source(self, ref: str):
        repo = self.github_api.repository(
            self.component_name.github_organisation(),
            self.component_name.github_repo()
        )
        url = repo._build_url('zipball', ref, base_url=repo._api)
        res = repo._get(url, verify=False, allow_redirects=True, stream=True)
        if not res.ok:
            raise RuntimeError('request to download github'
                               f' zip archive failed with {res.status_code=} {res.reason=}')

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
            remote_hash = project.get_custom_field('current_hash')

            current_hash = f'sha1:{sha1.hexdigest()}'
            if remote_hash and not remote_hash.startswith('sha1:'):
                raise NotImplementedError(remote_hash)

            if remote_hash != current_hash:
                self.client.upload_zipped_source_code(self.project_id, tmp_file)
                # project.set_custom_field(model.CustomFieldNames.ZIP_HASH.value, current_hash)
                # project.set_custom_field(model.CustomFieldNames.COMMIT_HASH.value, ref)
                # self.client.update_project(project)

    def start_scan_and_poll(self, **kwargs):
        scan_settings = checkmarx.model.ScanSettings(projectId=self.project_id, **kwargs)
        scan_id = self.client.start_scan(scan_settings=scan_settings)
        return self.client.wait_for_scan_result(scan_id=scan_id)

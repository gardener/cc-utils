import logging
import time

import ctx
import checkmarx.client
import checkmarx.model as model
import checkmarx.util


ctx.configure_default_logging()
logger = logging.getLogger(__name__)


def _create_or_get_project(
    client: checkmarx.client.CheckmarxClient,
    name: str,
    team_id: str,
    is_public: bool = True,
):
    try:
        project_id = client.get_project_id_by_name(project_name=name, team_id=team_id)
        return project_id
    except checkmarx.client.CXNotOkayException as e:
        if e.res.status_code == 404:
            return client.create_project(name, team_id, is_public).json().get('id')
        else:
            raise e


class CheckmarxProject:
    def __init__(
        self,
        artifact_name: str,
        checkmarx_client: checkmarx.client.CheckmarxClient,
        project_details: model.ProjectDetails,
    ):
        self.artifact_name = artifact_name
        self.client = checkmarx_client
        self.project_details = project_details

    def poll_and_retrieve_scan(self, scan_id: int):
        scan_response = self._poll_scan(scan_id=scan_id)

        if scan_response.status_value() is not model.ScanStatusValues.FINISHED:
            logger.error(f'scan for {self.artifact_name} failed with {scan_response.status=}')
            raise RuntimeError(f'Scan of artifact "{self.artifact_name}" finished with errors')

        clogger = checkmarx.util.component_logger(artifact_name=self.artifact_name)
        clogger.info('scan finished. Retrieving scan statistics')
        statistics = self.scan_statistics(scan_id=scan_response.id)

        return model.ScanResult(
            project_id=self.project_details.id,
            artifact_name=self.artifact_name,
            scan_response=scan_response,
            scan_statistic=statistics,
        )

    def update_remote_project(self):
        self.client.update_project(self.project_details)

    def start_scan(self):
        scan_settings = model.ScanSettings(projectId=self.project_details.id)
        return self.client.start_scan(scan_settings)

    def _poll_scan(self, scan_id: int, polling_interval_seconds=60):
        def scan_finished():
            scan = self.client.get_scan_state(scan_id=scan_id)
            clogger = checkmarx.util.component_logger(artifact_name=self.artifact_name)
            clogger.info(f'polling for {scan_id=}. {scan.status.name=}')
            if self.is_scan_finished(scan):
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

    def get_last_scans(self):
        return self.client.get_last_scans_of_project(project_id=self.project_details.id)

    def is_scan_finished(self, scan: model.ScanResponse):
        if model.ScanStatusValues(scan.status.id) in (
                model.ScanStatusValues.FINISHED,
                model.ScanStatusValues.FAILED,
                model.ScanStatusValues.CANCELED,
        ):
            return True
        elif model.ScanStatusValues(scan.status.id) in (
            model.ScanStatusValues.NEW,
            model.ScanStatusValues.PRE_SCAN,
            model.ScanStatusValues.QUEUED,
            model.ScanStatusValues.SCANNING,
            model.ScanStatusValues.POST_SCAN,
            model.ScanStatusValues.SOURCE_PULLING_AND_DEPLOYMENT,
        ):
            return False
        else:
            raise NotImplementedError

    def is_scan_necessary(
        self,
        hash: str,
    ):
        remote_hash = self.project_details.get_custom_field(
            model.CustomFieldKeys.HASH,
        )
        if remote_hash != hash:
            return True
        else:
            return False

    def upload_zip(self, file, raise_on_error: bool = True):
        res = self.client.upload_zipped_source_code(self.project_details.id, file)
        if raise_on_error:
            res.raise_for_status()
        return res


def init_checkmarx_project(
    checkmarx_client: checkmarx.client.CheckmarxClient,
    source_name: str,
    team_id: str,
) -> CheckmarxProject:

    project_name = source_name.replace('/', '_')

    project_id = _create_or_get_project(
        client=checkmarx_client,
        name=project_name,
        team_id=team_id,
    )

    project_details = checkmarx_client.get_project_by_id(project_id=project_id)

    return CheckmarxProject(
        checkmarx_client=checkmarx_client,
        artifact_name=source_name,
        project_details=project_details,
    )

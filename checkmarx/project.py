import logging
import time

import dacite

import ctx
import checkmarx.client
import checkmarx.model as model
import checkmarx.util

import gci.componentmodel as cm

ctx.configure_default_logging()
logger = logging.getLogger(__name__)


def init_checkmarx_project(
    checkmarx_client: checkmarx.client.CheckmarxClient,
    component: cm.Component,
    team_id: str,
):
    project_name = _calc_project_name_for_component(component_name=component.name)

    project_id = _create_or_get_project(
        client=checkmarx_client,
        name=project_name,
        team_id=team_id,
    )

    project_details = dacite.from_dict(
        model.ProjectDetails,
        checkmarx_client.get_project_by_id(project_id=project_id).json()
    )

    return CheckmarxProject(
        checkmarx_client=checkmarx_client,
        component_name=component.name,
        project_details=project_details,
    )


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


def _calc_project_name_for_component(component_name: str):
    return component_name.replace('/', '_')


class CheckmarxProject:
    def __init__(
        self,
        component_name: str,
        checkmarx_client: checkmarx.client.CheckmarxClient,
        project_details: model.ProjectDetails,
    ):
        self.component_name = component_name
        self.client = checkmarx_client
        self.project_details = project_details

    def poll_and_retrieve_scan(self, scan_id: int):
        scan_response = self._poll_scan(scan_id=scan_id)

        if scan_response.status_value() is not model.ScanStatusValues.FINISHED:
            logger.error(f'scan for {self.component_name} failed with {scan_response.status=}')
            raise RuntimeError('Scan did not finish successfully')

        clogger = checkmarx.util.component_logger(component_name=self.component_name)
        clogger.info('retrieving scan statistics')
        statistics = self.scan_statistics(scan_id=scan_response.id)

        return model.ScanResult(
            project_id=self.project_details.id,
            component_name=self.component_name,
            scan_response=scan_response,
            scan_statistic=statistics,
        )

    def get_project(self):
        return self.client.get_project_by_id(self.project_details.id).json()

    def update_remote_project(self):
        self.client.update_project(self.project_details)

    def start_scan(self):
        scan_settings = model.ScanSettings(projectId=self.project_details.id)
        return self.client.start_scan(scan_settings)

    def _poll_scan(self, scan_id: int, polling_interval_seconds=60):
        def scan_finished():
            scan = self.client.get_scan_state(scan_id=scan_id)
            clogger = checkmarx.util.component_logger(self.component_name)
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
        else:
            return False

    def is_scan_necessary(self, hash: str):
        remote_hash = self.project_details.get_custom_field(
            model.CustomFieldKeys.HASH,
        )
        if remote_hash != hash:
            print(f'{remote_hash=} != {hash=} - scan required')
            return True
        else:
            print(f'{remote_hash=} != {hash=} - scan not required')
            return False

    def upload_zip(self, file):
        r = self.client.upload_zipped_source_code(self.project_details.id, file)
        r.raise_for_status()

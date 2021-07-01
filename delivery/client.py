import requests

from dso.compliancedb.model import ScanTool
from dso.model import ArtifactType
import gci.componentmodel as cm

import ci.util


class DeliveryServiceRoutes:
    def __init__(self, base_url: str):
        self._base_url = base_url

    def component_descriptor(self):
        return 'http://' + ci.util.urljoin(
            self._base_url,
            'cnudie',
            'component',
        )

    def compliance_scan(self):
        return 'http://' + ci.util.urljoin(
            self._base_url,
            'compliance',
            'scan',
        )


class DeliveryServiceClient:
    def __init__(self, routes: DeliveryServiceRoutes):
        self._routes = routes

    def component_descriptor(
        self,
        name: str,
        version: str,
        ctx_repo_url: str,
        validation_mode: cm.ValidationMode=cm.ValidationMode.NONE,
    ):
        res = requests.get(
            url=self._routes.component_descriptor(),
            params={
                'component_name': name,
                'version': version,
                'ctx_repo_url': ctx_repo_url,
            },
        )

        res.raise_for_status()

        return cm.ComponentDescriptor.from_dict(
            res.json(),
            validation_mode=validation_mode,
        )

    def post_compliance_scan(
        self,
        scan_data: dict,
        compliancedb_cfg_name: str,
        tool: ScanTool,
        component_name: str,
        component_version: str,
        artifact_name: str,
        artifact_version: str,
        artifact_type: ArtifactType,
    ):
        body = {
            'scanData': scan_data,
            'complianceDbCfg': compliancedb_cfg_name,
            'tool': tool.value,
            'componentName': component_name,
            'componentVersion': component_version,
            'artifactName': artifact_name,
            'artifactVersion': artifact_version,
            'artifactType': artifact_type.value,
        }
        res = requests.post(
            url=self._routes.compliance_scan(),
            json=body,
        )

        res.raise_for_status()

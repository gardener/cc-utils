import dataclasses
import requests

import gci.componentmodel as cm

import ci.util
import dso.model


class DeliveryServiceRoutes:
    def __init__(self, base_url: str):
        self._base_url = base_url

    def component_descriptor(self):
        return 'http://' + ci.util.urljoin(
            self._base_url,
            'cnudie',
            'component',
        )

    def upload_metadata(self):
        return 'http://' + ci.util.urljoin(
            self._base_url,
            'artefacts',
            'upload-metadata',
        )


class DeliveryServiceClient:
    def __init__(
        self,
        routes: DeliveryServiceRoutes,
    ):
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

    def upload_metadata(
        self,
        data: dso.model.ComplianceData,
    ):
        res = requests.post(
            url=self._routes.upload_metadata(),
            json={'entries': [dataclasses.asdict(data)]},
        )

        res.raise_for_status()

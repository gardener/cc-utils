import requests

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

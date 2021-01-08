import requests

import ci.util
import model.saf


class SafClient:
    def __init__(self, saf_cfg: model.saf.SafApiCfg):
        self._saf_cfg = saf_cfg

    def post_evidence(self, raw: dict):
        res = requests.post(
            url=ci.util.urljoin(
                self._saf_cfg.base_url(),
                'data',
            ),
            headers={
                'Authorization': f'Bearer {self._saf_cfg.credentials().bearer_token}',
            },
            json=raw,
        )

        res.raise_for_status()

        return res

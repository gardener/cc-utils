import dataclasses
import enum
import json
import typing

import requests

import model.saf
import saf.model


class SafClient:
    def __init__(self, saf_cfg: model.saf.SafApiCfg):
        self._saf_cfg = saf_cfg

    def _post_evidence_dict(self, raw: dict):
        res = requests.post(
            url=self._saf_cfg.base_url(),
            headers={
                'Authorization': f'Bearer {self._saf_cfg.credentials().bearer_token}',
                'Content-Type': 'application/json',
            },
            data=json.dumps(raw, cls=EnumJSONEncoder),
        )

        res.raise_for_status()

        return res

    def post_evidence(
        self,
        evidence: typing.Union[saf.model.EvidenceRequest, dict],
    ):
        if dataclasses.is_dataclass(evidence):
            raw = dataclasses.asdict(evidence)
        elif isinstance(evidence, dict):
            raw = evidence

        return self._post_evidence_dict(raw=raw)


class EnumJSONEncoder(json.JSONEncoder):
    '''
    a json.JSONEncoder that will encode enum objects using their values
    '''
    def default(self, o):
        if isinstance(o, enum.Enum):
            return o.value
        return super().default(o)

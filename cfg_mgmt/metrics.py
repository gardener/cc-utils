import dataclasses
import datetime
import logging
import typing

import ccc.elasticsearch
import cfg_mgmt.model as cmm


logger = logging.getLogger(__name__)


def index_name(
    obj,
) -> str:
    if isinstance(obj, CcCfgComplianceResponsible):
        return 'cc_cfg_compliance_responsible'

    raise NotImplementedError(obj)


@dataclasses.dataclass(frozen=True)
class CcCfgComplianceResponsible:
    creation_date: str
    element_name: str
    element_type: str
    element_storage: str
    is_compliant: bool
    responsible_name: typing.List[str]
    responsible_type: typing.List[str]
    rotation_method: str

    @staticmethod
    def create(
        element_name: str,
        element_type: str,
        element_storage: str,
        is_compliant: bool,
        responsible: cmm.CfgResponsibleMapping,
        rotation_method: cmm.RotationMethod,
    ):
        '''
        convenience method to create a `CcCfgComplianceResponsible`
        '''
        names = []
        types = []
        if responsible:
            names = [resp.name for resp in responsible.responsibles]
            types = [resp.type.value for resp in responsible.responsibles]
        return CcCfgComplianceResponsible(
            creation_date=datetime.datetime.now().isoformat(),
            element_name=element_name,
            element_type=element_type,
            element_storage=element_storage,
            is_compliant=is_compliant,
            responsible_name=names,
            responsible_type=types,
            rotation_method=rotation_method.value,
        )


def metric_to_es(
    es_client: ccc.elasticsearch.ElasticSearchClient,
    metric,
):
    try:
        es_client.store_document(
            index=index_name(metric),
            body=dataclasses.asdict(metric),
            inject_metadata=False,
        )
    except Exception:
        import traceback
        logger.warning(traceback.format_exc())
        logger.warning('could not send route request to elastic search')

import dataclasses
import datetime
import logging
import typing

import cfg_mgmt.model as cmm


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CcCfgComplianceStorageResponsibles:
    creation_date: str
    compliant: int
    noncompliant: int
    responsible_name: str
    responsible_type: str
    url: str

    @staticmethod
    def create(
        url: str,
        responsible: cmm.CfgResponsible,
        compliant_count: int,
        non_compliant_count: int,
    ):
        '''
        convenience method to create a `CcCfgComplianceStorageResponsibles`
        '''
        return CcCfgComplianceStorageResponsibles(
            creation_date=datetime.datetime.now().isoformat(),
            compliant=compliant_count,
            noncompliant=non_compliant_count,
            responsible_name=responsible.name,
            responsible_type=responsible.type.value,
            url=url,
        )


@dataclasses.dataclass(frozen=True)
class CcCfgComplianceStatus:
    '''
    represents counts for (non)compliant cfg_elements for a url (element_storage)
    '''
    url: str
    compliant: int
    noncompliant: int
    creation_date: str

    @staticmethod
    def create(
        url: str,
        compliant_count: int,
        non_compliant_count: int,
    ):
        '''
        convenience method to create a `CcCfgComplianceStatus`
        '''
        return CcCfgComplianceStatus(
            url=url,
            compliant=compliant_count,
            noncompliant=non_compliant_count,
            creation_date=datetime.datetime.now().isoformat(),
        )


@dataclasses.dataclass(frozen=True)
class CcCfgComplianceResponsible:
    '''
    represents compliance status for a cfg_element per responsible
    '''
    creation_date: str
    element_name: str
    element_type: str
    element_storage: str
    is_compliant: bool
    responsible_name: typing.List[str]
    responsible_type: typing.List[str]
    rotation_method: str
    non_compliant_reasons: typing.List[str]

    @staticmethod
    def create(
        element_name: str,
        element_type: str,
        element_storage: str,
        is_compliant: bool,
        responsible: cmm.CfgResponsibleMapping,
        rotation_method: cmm.RotationMethod,
        non_compliant_reasons: typing.List[cmm.CfgElementPolicyViolation]
    ):
        '''
        convenience method to create a `CcCfgComplianceResponsible`
        '''
        names = []
        types = []
        if responsible:
            names = [resp.name for resp in responsible.responsibles]
            types = [resp.type.value for resp in responsible.responsibles]

        reasons = [
            reason.value
            for reason in non_compliant_reasons
        ]
        return CcCfgComplianceResponsible(
            creation_date=datetime.datetime.now().isoformat(),
            element_name=element_name,
            element_type=element_type,
            element_storage=element_storage,
            is_compliant=is_compliant,
            responsible_name=names,
            responsible_type=types,
            rotation_method=rotation_method.value,
            non_compliant_reasons=reasons,
        )


def index_name(
    obj: typing.Union[CcCfgComplianceStatus, CcCfgComplianceResponsible],
) -> str:
    if isinstance(obj, CcCfgComplianceResponsible):
        return 'cc_cfg_compliance_responsible'

    if isinstance(obj, CcCfgComplianceStatus):
        return 'cc_cfg_compliance_status'

    if isinstance(obj, CcCfgComplianceStorageResponsibles):
        return 'cc_cfg_compliance_storage_responsibles'

    raise NotImplementedError(obj)

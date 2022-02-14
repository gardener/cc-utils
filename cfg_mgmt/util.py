import datetime
import logging
import typing

import dateutil.parser

import cfg_mgmt.model as cmm
import cfg_mgmt.reporting as cmr
import ci.util
import model


logger = logging.getLogger(__name__)


def generate_cfg_element_status_reports(cfg_dir: str) -> list[cmr.CfgElementStatusReport]:
    ci.util.existing_dir(cfg_dir)

    cfg_factory = model.ConfigFactory._from_cfg_dir(
        cfg_dir,
        disable_cfg_element_lookup=True,
    )

    cfg_metadata = cmm.cfg_metadata_from_cfg_dir(cfg_dir)

    policies = cfg_metadata.policies
    rules = cfg_metadata.rules
    statuses = cfg_metadata.statuses
    responsibles = cfg_metadata.responsibles

    return [
        determine_status(
            element=element,
            policies=policies,
            rules=rules,
            statuses=statuses,
            responsibles=responsibles,
            element_storage=cfg_dir,
        ) for element in iter_cfg_elements(cfg_factory=cfg_factory)
    ]


def iter_cfg_elements(
    cfg_factory: typing.Union[model.ConfigFactory, model.ConfigurationSet],
    cfg_target: typing.Optional[cmm.CfgTarget] = None,
):
    if isinstance(cfg_factory, model.ConfigurationSet):
        type_names = cfg_factory.cfg_factory._cfg_types().keys()
    else:
        type_names = cfg_factory._cfg_types().keys()

    for type_name in type_names:
        # workaround: cfg-sets may reference non-local cfg-elements
        # also, cfg-elements only contain references to other cfg-elements
        # -> policy-checks will only add limited value
        if type_name == 'cfg_set':
            continue
        for cfg_element in cfg_factory._cfg_elements(cfg_type_name=type_name):
            if cfg_target and not cfg_target.matches(cfg_element):
                continue
            yield cfg_element


def iter_cfg_elements_requiring_rotation(
    cfg_elements: typing.Iterable[model.NamedModelElement],
    cfg_metadata: cmm.CfgMetadata,
    cfg_target: typing.Optional[cmm.CfgTarget]=None,
    element_filter: typing.Callable[[model.NamedModelElement], bool]=None,
    rotation_method: cmm.RotationMethod=None,
) -> typing.Generator[model.NamedModelElement, None, None]:
    for cfg_element in cfg_elements:
        if cfg_target and not cfg_target.matches(element=cfg_element):
            continue

        if element_filter and not element_filter(cfg_element):
            continue

        status = determine_status(
            element=cfg_element,
            policies=cfg_metadata.policies,
            rules=cfg_metadata.rules,
            responsibles=cfg_metadata.responsibles,
            statuses=cfg_metadata.statuses,
        )

        # hardcode rule: ignore elements w/o rule and policy
        if not status.policy or not status.rule:
            continue

        # hardcode: ignore all policies we cannot handle (currently, only MAX_AGE)
        if not status.policy.type is cmm.PolicyType.MAX_AGE:
            continue

        if rotation_method and status.policy.rotation_method is not rotation_method:
            continue

        # if there is no status, assume rotation be required
        if not status.status:
            yield cfg_element
            continue

        last_update = dateutil.parser.isoparse(status.status.credential_update_timestamp)
        if status.policy.check(last_update=last_update):
            continue
        else:
            yield cfg_element


def determine_status(
    element: model.NamedModelElement,
    policies: list[cmm.CfgPolicy],
    rules: list[cmm.CfgRule],
    responsibles: list[cmm.CfgResponsibleMapping],
    statuses: list[cmm.CfgStatus],
    element_storage: str=None,
) -> cmr.CfgElementStatusReport:
    for rule in rules:
        if rule.matches(element=element):
            break
    else:
        rule = None # no rule was configured

    rule: typing.Optional[cmm.CfgRule]

    if rule:
        for policy in policies:
            if policy.name == rule.policy:
                break
        else:
            rule = None # inconsistent cfg: rule with specified name does not exist

    for responsible in responsibles:
        if responsible.matches(element=element):
            break
    else:
        responsible = None

    for status in statuses:
        if status.matches(element):
            break
    else:
        status = None

    return cmr.CfgElementStatusReport(
        element_storage=element_storage,
        element_type=element._type_name,
        element_name=element._name,
        policy=policy,
        rule=rule,
        status=status,
        responsible=responsible,
    )


def cfg_report_summaries_to_es(
    es_client,
    cfg_report_summary_gen: typing.Generator[cmm.CfgReportingSummary, None, None],
):
    for cfg_report_summary in cfg_report_summary_gen:
        try:
            es_client.store_document(
                index='cc_cfg_compliance_status',
                body={
                    'url': cfg_report_summary.url,
                    'compliant': cfg_report_summary.compliantElementsCount,
                    'noncompliant': cfg_report_summary.noncompliantElementsCount,
                    'creation_date': datetime.datetime.now().isoformat()
                },
                inject_metadata=False,
            )
        except Exception:
            import traceback
            logger.warning(traceback.format_exc())
            logger.warning('could not send route request to elastic search')


def cfg_element_statuses_to_es(
    es_client,
    cfg_element_statuses: typing.Iterable[cmr.CfgElementStatusReport],
):
    for cfg_element_status in cfg_element_statuses:
        names = []
        types = []
        if cfg_element_status.responsible:
            names = [resp.name for resp in cfg_element_status.responsible.responsibles]
            types = [resp.type.value for resp in cfg_element_status.responsible.responsibles]

        report = list(cmr.create_report(
            cfg_element_statuses=[cfg_element_status],
            print_report=False,
        ))[0]
        try:
            es_client.store_document(
                index='cc_cfg_compliance_responsible',
                body={
                    'creation_date': datetime.datetime.now().isoformat(),
                    'element_name': cfg_element_status.element_name,
                    'element_type': cfg_element_status.element_type,
                    'element_storage': cfg_element_status.element_storage,
                    'responsible_name': names,
                    'responsible_type': types,
                    'is_compliant': bool(report.compliantElementsCount),
                },
                inject_metadata=False,
            )
        except Exception:
            import traceback
            logger.warning(traceback.format_exc())
            logger.warning('could not send route request to elastic search')

import logging
import typing

import dateutil.parser as dp

import cfg_mgmt.model as cmm
import cfg_mgmt.util as cmu
import ci.log
import ci.util
import model


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def evaluate_cfg_element_status(
    cfg_element_status: cmm.CfgElementStatusReport,
) -> cmm.CfgStatusEvaluationResult:

    fully_compliant = True
    has_responsible = True
    has_rule = True
    assigned_rule_refers_to_undefined_policy = False
    has_status = True
    requires_status = None
    credentials_outdated = None
    non_compliant_reasons = []

    if not cfg_element_status.responsible:
        fully_compliant = False
        has_responsible = False
        non_compliant_reasons.append(cmm.CfgElementPolicyViolation.NO_RESPONSIBLE)

    if not cfg_element_status.rule:
        fully_compliant = False
        has_rule = False
        non_compliant_reasons.append(cmm.CfgElementPolicyViolation.NO_RULE)

    elif not cfg_element_status.policy:
        fully_compliant = False
        assigned_rule_refers_to_undefined_policy = True
        non_compliant_reasons.append(
            cmm.CfgElementPolicyViolation.ASSIGNED_RULE_REFERS_TO_UNDEFINED_POLICY
        )

    elif cfg_element_status.policy.type is cmm.PolicyType.MAX_AGE:
        policy = cfg_element_status.policy

        # status is only required if policy requires rotation
        if policy.max_age is None:
            requires_status = False
        else:
            requires_status = True

        if requires_status:
            if not (status := cfg_element_status.status):
                fully_compliant = False
                has_status = False
                non_compliant_reasons.append(cmm.CfgElementPolicyViolation.NO_STATUS)

            else:
                last_update = dp.isoparse(status.credential_update_timestamp)

                if policy.check(last_update=last_update, honour_grace_period=True):
                    credentials_outdated = False
                else:
                    fully_compliant = False
                    credentials_outdated = True
                    non_compliant_reasons.append(cmm.CfgElementPolicyViolation.CREDENTIALS_OUTDATED)

    else:
        raise NotImplementedError(cfg_element_status.policy.type)

    return cmm.CfgStatusEvaluationResult(
        fullyCompliant=fully_compliant,
        hasResponsible=has_responsible,
        hasRule=has_rule,
        assignedRuleRefersToUndefinedPolicy=assigned_rule_refers_to_undefined_policy,
        hasStatus=has_status,
        requiresStatus=requires_status,
        credentialsOutdated=credentials_outdated,
        nonCompliantReasons=non_compliant_reasons,
    )


def create_report(
    cfg_element_statuses: typing.Iterable[cmm.CfgElementStatusReport],
):
    no_rule_assigned = []
    no_status = []
    assigned_rule_refers_to_undefined_policy = []
    no_responsible_assigned = []
    credentials_outdated = []
    credentials_not_outdated = []
    fully_compliant = []

    for cfg_element_status in cfg_element_statuses:
        evaluation_result = evaluate_cfg_element_status(cfg_element_status)

        if not evaluation_result.hasResponsible:
            no_responsible_assigned.append(cfg_element_status)

        if not evaluation_result.hasRule:
            no_rule_assigned.append(cfg_element_status)

        elif evaluation_result.assignedRuleRefersToUndefinedPolicy:
            assigned_rule_refers_to_undefined_policy.append(cfg_element_status)

        else:
            if evaluation_result.requiresStatus:
                if not evaluation_result.hasStatus:
                    no_status.append(cfg_element_status)

                else:
                    if evaluation_result.credentialsOutdated:
                        credentials_outdated.append(cfg_element_status)

                    else:
                        credentials_not_outdated.append(cfg_element_status)

        if evaluation_result.fullyCompliant:
            fully_compliant.append(cfg_element_status)

    def print_paragraph(header: str, statuses: typing.List[cmm.CfgElementStatusReport]):
        print(f'({len(statuses)}) {header}')
        print(2*'\n')

        for status in statuses:
            print(status.name)

        print('')
        print(40 * '-')

    print_paragraph(
        header='Cfg-Elements w/o assigned policy/rule',
        statuses=no_rule_assigned,
    )
    print_paragraph(
        header='Cfg-Elements w/o current status',
        statuses=no_status,
    )
    print_paragraph(
        header='Cfg-Elements with undefined policy',
        statuses=assigned_rule_refers_to_undefined_policy,
    )
    print_paragraph(
        header='Cfg-Elements w/o assigned responsible',
        statuses=no_responsible_assigned,
    )
    print_paragraph(
        header='Cfg-Elements w/ outdated credentials',
        statuses=credentials_outdated,
    )
    print_paragraph(
        header='Cfg-Elements with sufficiently recent credentials',
        statuses=credentials_not_outdated,
    )
    print_paragraph(
        header='Fully compliant cfg-elements *.*',
        statuses=fully_compliant,
    )


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

        status = cmu.determine_status(
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

        last_update = dp.isoparse(status.status.credential_update_timestamp)
        if status.policy.check(last_update=last_update, honour_grace_period=False):
            continue
        else:
            yield cfg_element


def generate_cfg_element_status_reports(
    cfg_dir: str,
    element_storage: str | None=None,
) -> list[cmm.CfgElementStatusReport]:
    '''
    If not passed explicitly, the element_storage defaults to cfg_dir.
    '''
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

    if not element_storage:
        element_storage = cfg_dir

    return [
        cmu.determine_status(
            element=element,
            policies=policies,
            rules=rules,
            statuses=statuses,
            responsibles=responsibles,
            element_storage=element_storage,
        ) for element in cmu.iter_cfg_elements(cfg_factory=cfg_factory)
    ]

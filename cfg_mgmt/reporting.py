import dataclasses
import logging
import typing

import dateutil.parser as dp

import cfg_mgmt.model as cmm
import ci.log
import ci.util


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


@dataclasses.dataclass
class CfgElementStatusReport:
    '''
    represents the current status of a configuration element

    primarily targeted for creating reports for human consumers
    '''
    element_storage: str # e.g. a github-url - not intended to be machine-readable
    element_type: str
    element_name: str

    policy: typing.Optional[cmm.CfgPolicy]
    rule: typing.Optional[cmm.CfgRule]
    responsible: typing.Optional[cmm.CfgResponsibleMapping]
    status: typing.Optional[cmm.CfgStatus]


def evaluate_cfg_element_status(
    cfg_element_status: CfgElementStatusReport,
) -> cmm.CfgStatusEvaluationResult:

    fully_compliant = True
    has_responsible = True
    has_rule = True
    assigned_rule_refers_to_undefined_policy = False
    has_status = True
    requires_status = None
    credentials_outdated = None

    if not cfg_element_status.responsible:
        fully_compliant = False
        has_responsible = False

    if not cfg_element_status.rule:
        fully_compliant = False
        has_rule = False

    elif not cfg_element_status.policy:
        fully_compliant = False
        assigned_rule_refers_to_undefined_policy = True

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

            else:
                last_update = dp.isoparse(status.credential_update_timestamp)

                if policy.check(last_update=last_update):
                    credentials_outdated = False
                else:
                    fully_compliant = False
                    credentials_outdated = True

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
    )


def create_report(
    cfg_element_statuses: typing.Iterable[CfgElementStatusReport],
    print_report: bool = True,
) -> typing.Generator[cmm.CfgReportingSummary, None, None]:
    no_rule_assigned = []
    no_status = []
    assigned_rule_refers_to_undefined_policy = []
    no_responsible_assigned = []
    credentials_outdated = []
    credentials_not_outdated = []
    fully_compliant = []
    compliance_summaries = dict()

    def compliance_summary(element_storage: str):
        if (summary := compliance_summaries.get(element_storage)):
            return summary

        cfg_reporting_summary = cmm.CfgReportingSummary(
            url=element_storage,
            noRuleAssigned=[],
            noStatus=[],
            assignedRuleRefersToUndefinedPolicy=[],
            noResponsibleAssigned=[],
            credentialsOutdated=[],
            credentialsNotOutdated=[],
            fullyCompliant=[],
        )
        compliance_summaries[element_storage] = cfg_reporting_summary
        return cfg_reporting_summary

    for cfg_element_status in cfg_element_statuses:
        cfg_summary = compliance_summary(element_storage=cfg_element_status.element_storage)
        evaluation_result = evaluate_cfg_element_status(cfg_element_status)

        if not evaluation_result.hasResponsible:
            no_responsible_assigned.append(cfg_element_status)
            cfg_summary.noResponsibleAssigned.append(cfg_element_status)

        if not evaluation_result.hasRule:
            no_rule_assigned.append(cfg_element_status)
            cfg_summary.noRuleAssigned.append(cfg_element_status)

        elif evaluation_result.assignedRuleRefersToUndefinedPolicy:
            assigned_rule_refers_to_undefined_policy.append(cfg_element_status)
            cfg_summary.assignedRuleRefersToUndefinedPolicy.append(cfg_element_status)

        else:
            if evaluation_result.requiresStatus:
                if not evaluation_result.hasStatus:
                    no_status.append(cfg_element_status)
                    cfg_summary.noStatus.append(cfg_element_status)

                else:
                    if evaluation_result.credentialsOutdated:
                        credentials_outdated.append(cfg_element_status)
                        cfg_summary.credentialsOutdated.append(cfg_element_status)

                    else:
                        credentials_not_outdated.append(cfg_element_status)
                        cfg_summary.credentialsNotOutdated.append(cfg_element_status)

        if evaluation_result.fullyCompliant:
            fully_compliant.append(cfg_element_status)
            cfg_summary.fullyCompliant.append(cfg_element_status)
            cfg_summary.compliantElementsCount += 1

        else:
            cfg_summary.noncompliantElementsCount += 1

    yield from compliance_summaries.values()

    def cfg_element_status_name(status: CfgElementStatusReport):
        return f'{status.element_storage}/{status.element_type}/{status.element_name}'

    def print_paragraph(header: str, statuses: typing.List[CfgElementStatusReport]):
        print(f'({len(statuses)}) {header}')
        print(2*'\n')

        for status in statuses:
            print(cfg_element_status_name(status=status))

        print('')
        print(40 * '-')

    if not print_report:
        return

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

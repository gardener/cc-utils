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


def analyse_cfg_element_status(
    cfg_element_status: CfgElementStatusReport,
) -> cmm.CfgStatusAnalysis:

    analysis = cmm.CfgStatusAnalysis(
        fullyCompliant=True,
        hasResponsible=True,
        hasRule=True,
        assignedRuleRefersToUndefinedPolicy=False,
        hasStatus=True,
    )

    if not cfg_element_status.responsible:
        analysis.fullyCompliant = False
        analysis.hasResponsible = False

    if not cfg_element_status.rule:
        analysis.fullyCompliant = False
        analysis.hasRule = False

    elif not cfg_element_status.policy:
        analysis.fullyCompliant = False
        analysis.assignedRuleRefersToUndefinedPolicy = True

    elif cfg_element_status.policy.type is cmm.PolicyType.MAX_AGE:
        policy = cfg_element_status.policy

        # status is only required if policy requires rotation
        if policy.max_age is None:
            analysis.requiresStatus = False
        else:
            analysis.requiresStatus = True

        if analysis.requiresStatus:
            if not (status := cfg_element_status.status):
                analysis.fullyCompliant = False
                analysis.hasStatus = False

            else:
                last_update = dp.isoparse(status.credential_update_timestamp)

                if policy.check(last_update=last_update):
                    analysis.credentialsOutdated = False
                else:
                    analysis.fullyCompliant = False
                    analysis.credentialsOutdated = True

    else:
        raise NotImplementedError(cfg_element_status.policy.type)

    return analysis


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
        analysis = analyse_cfg_element_status(cfg_element_status)

        if not analysis.hasResponsible:
            no_responsible_assigned.append(cfg_element_status)
            cfg_summary.noResponsibleAssigned.append(cfg_element_status)

        if not analysis.hasRule:
            no_rule_assigned.append(cfg_element_status)
            cfg_summary.noRuleAssigned.append(cfg_element_status)

        elif analysis.assignedRuleRefersToUndefinedPolicy:
            assigned_rule_refers_to_undefined_policy.append(cfg_element_status)
            cfg_summary.assignedRuleRefersToUndefinedPolicy.append(cfg_element_status)

        else:
            if analysis.requiresStatus:
                if not analysis.hasStatus:
                    no_status.append(cfg_element_status)
                    cfg_summary.noStatus.append(cfg_element_status)

                else:
                    if analysis.credentialsOutdated:
                        credentials_outdated.append(cfg_element_status)
                        cfg_summary.credentialsOutdated.append(cfg_element_status)

                    else:
                        credentials_not_outdated.append(cfg_element_status)
                        cfg_summary.credentialsNotOutdated.append(cfg_element_status)

        if analysis.fullyCompliant:
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

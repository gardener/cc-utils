import datetime
import enum
import functools
import logging
import textwrap
import time
import typing
import urllib.parse

import cachetools
import github3
import github3.issues
import github3.issues.milestone
import github3.issues.issue
import github3.orgs
import github3.repos
import github3.users

import gci.componentmodel as cm
import requests

import ccc.delivery
import ccc.github
import checkmarx.model
import cfg_mgmt.model as cmm
import cfg_mgmt.reporting as cmr
import ci.util
import clamav.scan
import cnudie.util
import concourse.model.traits.image_scan as image_scan
import delivery.client
import delivery.model
import github.codeowners
import github.compliance.issue
import github.compliance.milestone
import github.compliance.model as gcm
import github.retry
import github.user
import github.util
import model.delivery
import protecode.model as pm

logger = logging.getLogger(__name__)

_compliance_label_vulnerabilities = github.compliance.issue._label_bdba
_compliance_label_licenses = github.compliance.issue._label_licenses
_compliance_label_os_outdated = github.compliance.issue._label_os_outdated
_compliance_label_checkmarx = github.compliance.issue._label_checkmarx
_compliance_label_malware = github.compliance.issue._label_malware
_compliance_label_credentials_outdated = github.compliance.issue._label_outdated_credentials
_compliance_label_no_responsible = github.compliance.issue._label_no_responsible
_compliance_label_no_rule = github.compliance.issue._label_no_rule
_compliance_label_no_status = github.compliance.issue._label_no_status
_compliance_label_undefined_policy = github.compliance.issue._label_undefined_policy

_ctx_label_prefix = github.compliance.issue._label_prefix_ctx


def _criticality_label(classification: gcm.Severity):
    return f'compliance-priority/{str(classification)}'


@cachetools.cached(cache={})
@github.retry.retry_and_throttle
def _all_issues(
    repository,
):
    return set(repository.issues())


def _criticality_classification(cve_score: float) -> gcm.Severity:
    if not cve_score or cve_score <= 0:
        return None

    if cve_score < 4.0:
        return gcm.Severity.LOW
    if cve_score < 7.0:
        return gcm.Severity.MEDIUM
    if cve_score < 9.0:
        return gcm.Severity.HIGH
    if cve_score >= 9.0:
        return gcm.Severity.CRITICAL


def _delivery_dashboard_url(
    component: cm.Component,
    base_url: str,
):
    url = ci.util.urljoin(
        base_url,
        '#/component'
    )

    query = urllib.parse.urlencode(
        query={
            'name': component.name,
            'version': component.version,
            'view': 'bom',
        }
    )

    return f'{url}?{query}'


def _pluralise(prefix: str, count: int):
    if count == 1:
        return prefix
    return f'{prefix}s'


def _compliance_status_summary(
    component: cm.Component,
    artifacts: typing.Sequence[cm.Artifact],
    report_urls: str,
    issue_description: str,
    issue_value: str,
):
    if isinstance(artifacts[0].type, enum.Enum):
        artifact_type = artifacts[0].type.value
    else:
        artifact_type = artifacts[0].type

    artifact_versions = ', '.join((r.version for r in artifacts))

    report_urls = '\n- '.join(report_urls)

    summary = textwrap.dedent(f'''\
        # Compliance Status Summary

        |    |    |
        | -- | -- |
        | Component | {component.name} |
        | Component-Version | {component.version} |
        | Artifact  | {artifacts[0].name} |
        | {_pluralise('Artifact-Version', len(artifacts))}  | {artifact_versions} |
        | Artifact-Type | {artifact_type} |
        | {issue_description} | {issue_value} |

        The aforementioned {_pluralise(artifact_type, len(artifacts))} yielded findings
        relevant for future release decisions.

        For viewing detailed scan {_pluralise('report', len(artifacts))}, see the following
        {_pluralise('Scan Report', len(artifacts))}:
    ''')

    return summary + '- ' + report_urls


def _ocm_result_group_template_vars(
    result_group: gcm.ScanResultGroup,
    delivery_dashboard_url: str,
) -> dict:
    component = result_group.component
    artifact_name = result_group.artifact
    artifacts = [gcm.artifact_from_node(res.scanned_element) for res in result_group.results]

    artifact_versions = ', '.join((artifact.version for artifact in artifacts))
    artifact_types = ', '.join(set(
        (
            artifact.type.value
            if isinstance(artifact.type, enum.Enum)
            else artifact.type
            for artifact in artifacts
        )
    ))

    return {
        'component_name': component.name,
        'component_version': component.version,
        'resource_name': artifact_name, # TODO: to be removed at some point use artifact_name instead
        'resource_version': artifact_versions, # TODO: to be removed use artifact_version instead
        'resource_type': artifact_types,       # TODO: to be removed use artifact_type instead
        'artifact_name': artifact_name,
        'artifact_version': artifact_versions,
        'artifact_type': artifact_types,
        'delivery_dashboard_url': delivery_dashboard_url,
    }


def _vulnerability_template_vars(
    result_group: gcm.ScanResultGroup,
) -> dict:
    results: tuple[pm.BDBA_ScanResult] = result_group.results_with_findings
    analysis_results = [r.result for r in results]
    greatest_cve = max((r.greatest_cve_score for r in results))
    component = result_group.component
    artifacts = [gcm.artifact_from_node(res.scanned_element) for res in result_group.results]

    return {
        'summary': _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=greatest_cve,
            issue_description='Greatest CVE Score',
            report_urls=[ar.report_url() for ar in analysis_results],
        ),
        'greatest_cve': greatest_cve,
        'criticality_classification': str(_criticality_classification(
            cve_score=greatest_cve
        )),
    }


def _license_template_vars(
    result_group: gcm.ScanResultGroup,
    license_cfg: image_scan.LicenseCfg,
) -> dict:
    results: tuple[pm.BDBA_ScanResult] = result_group.results_with_findings
    analysis_results = [r.result for r in results]
    prohibited_licenses = set()
    all_licenses = set()
    component = result_group.component
    artifacts = [gcm.artifact_from_node(res.scanned_element) for res in result_group.results]

    for r in results:
        all_licenses |= r.license_names

    for license_name in all_licenses:
        if not license_cfg.is_allowed(license_name):
            prohibited_licenses.add(license_name)

    return {
        'summary': _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=' ,'.join(prohibited_licenses),
            issue_description='Prohibited Licenses',
            report_urls=[ar.report_url() for ar in analysis_results],
        ),
        'criticality_classification': str(gcm.Severity.BLOCKER),
    }


def _os_info_template_vars(
    result_group: gcm.ScanResultGroup,
) -> dict:
    worst_result = result_group.worst_result
    worst_result: gcm.OsIdScanResult
    os_info = worst_result.os_id
    os_name_and_version = f'{os_info.ID}:{os_info.VERSION_ID}'
    component = result_group.component
    artifacts = [gcm.artifact_from_node(res.scanned_element) for res in result_group.results]

    return {
        'summary': _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=os_name_and_version,
            issue_description='Outdated OS-Version',
            report_urls=(),
        ),
    }


def _checkmarx_template_vars(
    result_group: gcm.ScanResultGroup,
) -> dict:

    def iter_report_urls():
        for r in results:
            name = f'{r.scanned_element.source.name}:{r.scanned_element.source.version}'
            yield f'[Assessments for {name}]({r.report_url})'
            yield f'[Summary for {name}]({r.overview_url})'

    results: tuple[checkmarx.model.ScanResult] = result_group.results_with_findings
    worst_result: checkmarx.model.ScanResult = result_group.worst_result
    stat = worst_result.scan_statistic
    summary_str = (f'Findings: High: {stat.highSeverity}, Medium: {stat.mediumSeverity}, '
        f'Low: {stat.lowSeverity}, Info: {stat.infoSeverity}')
    artifacts = [gcm.artifact_from_node(res.scanned_element) for res in result_group.results]
    component = result_group.component

    crit = (f'Risk: {worst_result.scan_response.scanRisk}, '
        f'Risk Severity: {worst_result.scan_response.scanRiskSeverity}')

    return {
        'summary': _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=summary_str,
            issue_description='Checkmarx Scan Summary',
            report_urls=tuple(iter_report_urls()),
        ),
        'criticality_classification': crit,
    }


def _malware_template_vars(
    result_group: gcm.ScanResultGroup,
) -> dict:
    results: tuple[clamav.scan.ClamAV_ResourceScanResult] = result_group.results_with_findings
    summary_str = ''.join((
        result.scan_result.summary() for result in results
    )).replace('\n', '')
    component = result_group.component
    artifacts = [gcm.artifact_from_node(res.scanned_element) for res in result_group.results]

    return {
        'summary': _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=summary_str,
            issue_description='ClamAV Scan Result',
            report_urls=(),
        ),
        'criticality_classification': str(gcm.Severity.BLOCKER),
    }


def _cfg_policy_violation_template_vars(result_group: gcm.ScanResultGroup) -> dict:
    results: tuple[gcm.CfgScanResult] = result_group.results_with_findings
    result = results[0]

    if result.scanned_element.responsible:
        # remove foremost "@" to prevent notification mails
        responsibles = '<br/>'.join([
            f'{r.name.removeprefix("@")} ({r.type.value})'
            for r in result.scanned_element.responsible.responsibles
        ])
        responsibles_len = len(result.scanned_element.responsible.responsibles)

    else:
        responsibles = 'unknown'
        responsibles_len = 1

    element_storage = result.scanned_element.element_storage

    summary = textwrap.dedent(f'''\
        # Compliance Status Summary
        |    |    |
        | -- | -- |
        | Element Storage | [{element_storage}](https://{element_storage}) |
        | Element Type | {result.scanned_element.element_type} |
        | Element Name | {result.scanned_element.element_name} |
        | {_pluralise("Responsible", responsibles_len)} | {responsibles} |
    ''')

    return {
        'summary': summary
    }


def _template_vars(
    result_group: gcm.ScanResultGroup,
    license_cfg: image_scan.LicenseCfg,
    delivery_dashboard_url: str='',
) -> dict:
    scanned_element = result_group.results[0].scanned_element
    issue_type = result_group.issue_type

    if gcm.is_ocm_artefact_node(scanned_element):
        template_variables = _ocm_result_group_template_vars(
            result_group=result_group,
            delivery_dashboard_url=delivery_dashboard_url,
        )

    elif isinstance(scanned_element, cmr.CfgElementStatusReport):
        template_variables = {
            'cfg_element_name': scanned_element.element_name,
            'cfg_element_type': scanned_element.element_type,
            'cfg_element_storage': scanned_element.element_storage,
            'cfg_element_qualified_name': scanned_element.name,
        }

    else:
        raise TypeError(result_group)

    if issue_type == _compliance_label_vulnerabilities:
        template_variables |= _vulnerability_template_vars(result_group)

    elif issue_type == _compliance_label_licenses:
        template_variables |= _license_template_vars(
            result_group=result_group,
            license_cfg=license_cfg,
        )

    elif issue_type == _compliance_label_os_outdated:
        template_variables |= _os_info_template_vars(result_group)

    elif issue_type == _compliance_label_checkmarx:
        template_variables |= _checkmarx_template_vars(result_group)

    elif issue_type == _compliance_label_malware:
        template_variables |= _malware_template_vars(result_group)

    elif issue_type in (
        _compliance_label_credentials_outdated,
        _compliance_label_no_responsible,
        _compliance_label_no_rule,
        _compliance_label_no_status,
        _compliance_label_undefined_policy,
    ):
        template_variables |= _cfg_policy_violation_template_vars(result_group)

    else:
        raise NotImplementedError(issue_type)

    return template_variables


def _scanned_element_repository(
    scanned_element: gcm.Target,
) -> github3.repos.repo.Repository:
    if gcm.is_ocm_artefact_node(scanned_element):
        source = cnudie.util.main_source(component=scanned_element.component)

        if not source.access.type is cm.AccessType.GITHUB:
            raise NotImplementedError(source)

        org = source.access.org_name()
        name = source.access.repository_name()
        gh_api = ccc.github.github_api(repo_url=source.access.repoUrl)

        return gh_api.repository(org, name)

    elif isinstance(scanned_element, cmr.CfgElementStatusReport):
        gh_api = ccc.github.github_api(repo_url=scanned_element.element_storage)

        parsed_url = ci.util.urlparse(scanned_element.element_storage)
        path = parsed_url.path.strip('/')
        org, repo = path.split('/')

        return gh_api.repository(org, repo)

    else:
        raise TypeError(scanned_element)


def _scanned_element_assignees(
    scanned_element: gcm.Target,
    delivery_svc_client: delivery.client.DeliveryServiceClient | None,
    repository: github3.repos.repo.Repository,
    gh_api: github3.GitHub | github3.GitHubEnterprise,
) -> tuple[str]:

    def iter_gh_usernames_from_responsibles_mapping(
        gh_api: github3.GitHub | github3.GitHubEnterprise,
        responsibles_mapping: cmm.CfgResponsibleMapping,
    ) -> typing.Generator[github.codeowners.Username, None, None]:
        unique_usernames = set()
        for responsible in responsibles_mapping.responsibles:
            if responsible.type == cmm.CfgResponsibleType.EMAIL:
                for username in github.codeowners.usernames_from_email_address(
                    email_address=responsible.name,
                    gh_api=gh_api,
                ):
                    unique_usernames.add(username)
            elif responsible.type == cmm.CfgResponsibleType.GITHUB:
                parsed = github.codeowners.parse_codeowner_entry(responsible.name)
                for username in github.codeowners.resolve_usernames(
                    codeowners_entries=[parsed],
                    github_api=gh_api,
                ):
                    unique_usernames.add(username)
            else:
                logger.warning(f'unable to process {responsible.type=}')
        yield from unique_usernames

    if gcm.is_ocm_artefact_node(scanned_element):
        if not delivery_svc_client:
            return ()

        artifact = gcm.artifact_from_node(scanned_element)
        try:
            assignees = delivery.client.github_users_from_responsibles(
                responsibles=delivery_svc_client.component_responsibles(
                    component=scanned_element.component,
                    artifact=artifact,
                ),
                github_url=repository.url,
            )

            return tuple((
                u.username for u in assignees
                if github.user.is_user_active(
                    username=u.username,
                    github=gh_api,
                )
            ))
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f'Delivery Service returned 404 for '
                    f'{scanned_element.component.name=}, {artifact.name=}')
                return ()
            else:
                raise

    elif isinstance(scanned_element, cmr.CfgElementStatusReport):
        if not scanned_element.responsible:
            return ()

        return tuple((
            username for username in iter_gh_usernames_from_responsibles_mapping(
                responsibles_mapping=scanned_element.responsible,
                gh_api=gh_api,
            )
            if github.user.is_user_active(
                username=username,
                github=gh_api,
            )
        ))
    else:
        raise TypeError(scanned_element)


def _scanned_element_title(
    scanned_element: gcm.Target,
    issue_type: str,
) -> str:
    if gcm.is_ocm_artefact_node(scanned_element):
        artifact = gcm.artifact_from_node(scanned_element)
        return f'[{issue_type}] - {scanned_element.component.name}:{artifact.name}'

    elif isinstance(scanned_element, cmr.CfgElementStatusReport):
        return f'[{issue_type}] - {scanned_element.name}'

    else:
        raise TypeError(scanned_element)


def _scanned_element_ctx_label(
    scanned_element: gcm.Target,
) -> tuple[str]:
    if gcm.is_ocm_artefact_node(scanned_element):
        return ()

    elif isinstance(scanned_element, cmr.CfgElementStatusReport):
        digest_label = github.compliance.issue.digest_label(
            prefix=_ctx_label_prefix,
            digest_str=scanned_element.element_storage,
        )
        return (digest_label, )

    else:
        raise TypeError(scanned_element)


@functools.cache
def _target_milestone(
    repo: github3.repos.Repository,
    sprint: delivery.model.Sprint,
):
    return github.compliance.milestone.find_or_create_sprint_milestone(
        repo=repo,
        sprint=sprint,
    )


@functools.cache
def _target_sprint(
    delivery_svc_client: delivery.client.DeliveryServiceClient,
    latest_processing_date: datetime.date,
):
    try:
        target_sprint = delivery_svc_client.sprint_current(before=latest_processing_date)
    except requests.HTTPError as http_error:
        logger.warning(f'error determining tgt-sprint {http_error=} - falling back to current')
        target_sprint = delivery_svc_client.sprint_current()

    return target_sprint


class PROCESSING_ACTION(enum.Enum):
    DISCARD = 'discard'
    REPORT = 'report'


def create_or_update_github_issues(
    result_group_collection: gcm.ScanResultGroupCollection,
    max_processing_days: gcm.MaxProcessingTimesDays,
    gh_api: github3.GitHub=None,
    overwrite_repository: github3.repos.Repository=None,
    preserve_labels_regexes: typing.Iterable[str]=(),
    github_issue_template_cfgs: list[image_scan.GithubIssueTemplateCfg]=None,
    delivery_svc_client: delivery.client.DeliveryServiceClient=None,
    delivery_svc_endpoints: model.delivery.DeliveryEndpointsCfg=None,
    license_cfg: image_scan.LicenseCfg=None, # XXX -> callback
    gh_quota_minimum: int = 2000, # skip issue updates if remaining quota falls below this threshold
):
    # workaround / hack:
    # we map findings to <component-name>:<resource-name>
    # in case of ambiguities, this would lead to the same ticket firstly be created, then closed
    # -> do not close tickets in this case.
    # a cleaner approach would be to create seperate tickets, or combine findings into shared
    # tickets. For the time being, this should be "good enough"

    result_groups = result_group_collection.result_groups
    result_groups_with_findings = result_group_collection.result_groups_with_findings
    result_groups_without_findings = result_group_collection.result_groups_without_findings

    err_count = 0

    def is_remaining_quota_too_low() -> bool:
        ratelimit_remaining = gh_api.ratelimit_remaining
        logger.info(f'{ratelimit_remaining=}')
        if ratelimit_remaining < gh_quota_minimum:
            return True
        return False

    def process_result(
        result_group: gcm.ScanResultGroup,
        action: PROCESSING_ACTION,
    ):
        nonlocal gh_api
        nonlocal err_count
        issue_type = result_group.issue_type

        if action == PROCESSING_ACTION.DISCARD:
            results = result_group.results_without_findings
        elif action == PROCESSING_ACTION.REPORT:
            results = result_group.results_with_findings

        criticality_classification = result_group.worst_severity

        scan_result = result_group.results[0]
        ctx_labels = _scanned_element_ctx_label(scan_result.scanned_element)

        if gcm.is_ocm_artefact_node(scan_result.scanned_element):
            if not len({r.scanned_element.component.name for r in results}) == 1:
                raise ValueError('not all component names are identical')

        if overwrite_repository:
            repository = overwrite_repository
        else:
            repository = _scanned_element_repository(scan_result.scanned_element)

        known_issues = _all_issues(repository)

        if action == PROCESSING_ACTION.DISCARD:
            github.compliance.issue.close_issue_if_present(
                scanned_element=scan_result.scanned_element,
                issue_type=issue_type,
                repository=repository,
                known_issues=known_issues,
                ctx_labels=ctx_labels,
            )

            element_name = github.compliance.issue.name_for_element(scan_result.scanned_element)
            logger.info(f'closed (if existing) gh-issue for {element_name=}')

        elif action == PROCESSING_ACTION.REPORT:
            assignees = _scanned_element_assignees(
                scanned_element=scan_result.scanned_element,
                delivery_svc_client=delivery_svc_client,
                repository=repository,
                gh_api=gh_api,
            )

            target_milestone = None
            latest_processing_date = None

            if delivery_svc_client:
                try:
                    max_days = max_processing_days.for_severity(
                        criticality_classification
                    )

                    latest_processing_date = datetime.date.today() + \
                        datetime.timedelta(days=max_days)

                    target_sprint = _target_sprint(
                        delivery_svc_client=delivery_svc_client,
                        latest_processing_date=latest_processing_date,
                    )
                    target_milestone = _target_milestone(
                        repo=repository,
                        sprint=target_sprint,
                    )
                except Exception as e:
                    logger.warning(f'{e=}')
                    target_milestone = None

            if gcm.is_ocm_artefact_node(scan_result.scanned_element) and delivery_svc_endpoints:
                delivery_dashboard_url = _delivery_dashboard_url(
                    component=scan_result.scanned_element.component,
                    base_url=delivery_svc_endpoints.dashboard_url(),
                )
                delivery_dashboard_url = f'[Delivery-Dashboard]({delivery_dashboard_url})'
            else:
                delivery_dashboard_url = ''

            template_variables = _template_vars(
                result_group=result_group,
                license_cfg=license_cfg,
                delivery_dashboard_url=delivery_dashboard_url,
            )

            for issue_cfg in github_issue_template_cfgs:
                if issue_cfg.type == issue_type:
                    break
            else:
                raise ValueError(f'no template for {issue_type=}')

            body = issue_cfg.body.format(**template_variables)

            try:
                issue = github.compliance.issue.create_or_update_issue(
                    scanned_element=scan_result.scanned_element,
                    issue_type=issue_type,
                    repository=repository,
                    body=body,
                    assignees=assignees,
                    milestone=target_milestone,
                    latest_processing_date=latest_processing_date,
                    extra_labels=(
                        _criticality_label(classification=criticality_classification),
                    ),
                    ctx_labels=ctx_labels,
                    preserve_labels_regexes=preserve_labels_regexes,
                    known_issues=known_issues,
                    title=_scanned_element_title(
                        scanned_element=scan_result.scanned_element,
                        issue_type=issue_type,
                    ),
                )
                if result_group.comment_callback:
                    def single_comment(result: gcm.ScanResult):
                        if gcm.is_ocm_artefact_node(result.scanned_element):
                            a = gcm.artifact_from_node(result.scanned_element)
                            header = f'**{a.name}:{a.version}**\n'

                        elif isinstance(result.scanned_element, cmr.CfgElementStatusReport):
                            header = '**Policy Violations**\n'

                        else:
                            raise TypeError(result)

                        return header + result_group.comment_callback(result)

                    def sortable_result_str(result: gcm.ScanResult):
                        if gcm.is_ocm_artefact_node(result.scanned_element):
                            c = result.scanned_element.component
                            a = gcm.artifact_from_node(result.scanned_element)
                            result_str = f'{c.name}:{c.version}/{a.name}:{a.version}'

                        elif isinstance(result.scanned_element, cmr.CfgElementStatusReport):
                            result_str = result.scanned_element.name

                        else:
                            raise TypeError(result)

                        return result_str

                    sorted_results = sorted(
                        results,
                        key=sortable_result_str,
                    )

                    comment_body = '\n'.join((single_comment(result) for result in sorted_results))

                    # only add comment if not already present
                    for comment in issue.comments():
                        if comment.body.strip() == comment_body.strip():
                            break
                    else:
                        issue.create_comment(comment_body)

                element_name = github.compliance.issue.name_for_element(scan_result.scanned_element)
                logger.info(
                    f'updated gh-issue for {element_name=} '
                    f'{issue_type=}: {issue.html_url=}'
                )
            except github3.exceptions.GitHubError as ghe:
                err_count += 1
                logger.warning('error whilst trying to create or update issue - will keep going')
                logger.warning(f'error: {ghe} {ghe.code=} {ghe.message=}')

        else:
            raise NotImplementedError(action)

    if is_remaining_quota_too_low():
        logger.warning(f'skipping issue updates, quota too low; {gh_quota_minimum=}')
        return

    for result_group in result_groups_with_findings:
        process_result(
            result_group=result_group,
            action=PROCESSING_ACTION.REPORT,
        )
        time.sleep(1) # throttle github-api-requests

    if is_remaining_quota_too_low():
        logger.warning(f'skipping issue updates, quota too low; {gh_quota_minimum=}')
        return

    for result_group in result_groups_without_findings:
        logger.info(f'discarding issue for {result_group.name=} vulnerabilities')
        process_result(
            result_group=result_group,
            action=PROCESSING_ACTION.DISCARD,
        )
        time.sleep(1) # throttle github-api-requests

    if groups_with_scan_error := result_group_collection.result_groups_with_scan_errors:
        logger.warning(f'{len(groups_with_scan_error)=} had scanning errors (check log)')
        # do not fail job (for now); might choose to, later

    if is_remaining_quota_too_low():
        logger.warning(f'skipping issue updates, quota too low; {gh_quota_minimum=}')
        return

    if overwrite_repository:
        known_issues = _all_issues(overwrite_repository)
        issue_type = result_group_collection.issue_type

        all_ctx_labels = set()

        if result_groups:
            for result_group in result_groups:
                scanned_element = result_group.results[0].scanned_element
                if (ctx_labels := _scanned_element_ctx_label(scanned_element)):
                    all_ctx_labels = all_ctx_labels | set(ctx_labels)

        else:
            logger.info('no scan results, will skip issues with ctx label')

        close_issues_for_absent_resources(
            result_groups=result_groups,
            known_issues=known_issues,
            issue_type=issue_type,
            ctx_labels=all_ctx_labels,
        )

    if err_count > 0:
        logger.warning(f'{err_count=} - there were errors - will raise')
        raise ValueError('not all gh-issues could be created/updated/deleted')

    logger.info(f'{gh_api.ratelimit_remaining=}')


def close_issues_for_absent_resources(
    result_groups: list[gcm.ScanResultGroup],
    known_issues: typing.Iterator[github3.issues.issue.ShortIssue],
    issue_type: str | None,
    ctx_labels: typing.Iterable[str]=(),
):
    '''
    closes all open issues for scanned elements that are not present in given result-groups.

    this is intended to automatically close issues for scan targets that are no longer present.
    '''

    def close_issues(
        issues: typing.Iterable[github3.issues.Issue],
    ):
        for issue in issues:
            logger.info(
                f"Closing issue '{issue.title}'({issue.html_url}) since no scan contained a "
                "scanned element matching its digest."
            )
            issue.create_comment('closing, because scanned element no longer present in BoM')
            issue.close()

    def has_ctx_label(
        issue: github3.issues.Issue,
    ) -> bool:
        return any([
            l.name.startswith(_ctx_label_prefix)
            for l in issue.original_labels]
        )

    all_issues = github.compliance.issue.enumerate_issues(
        scanned_element=None,
        issue_type=issue_type,
        known_issues=known_issues,
        state='open',
        extra_labels=ctx_labels,
    )

    if not ctx_labels:
        all_issues = (
            issue
            for issue in all_issues
            if not has_ctx_label(issue)
        )

    if not result_groups:
        logger.info(f'no scan results, will close all issues for {issue_type=} and {ctx_labels=}')
        close_issues(all_issues)
        return

    scanned_element = result_groups[0].results[0].scanned_element
    prefix = github.compliance.issue.prefix_for_element(scanned_element)

    def component_resource_label(issue: github3.issues.Issue) -> str:
        for label in issue.original_labels:
            label: github3.issues.label.ShortLabel
            if label.name.startswith(prefix):
                return label.name

    component_resources_to_issues = {
        component_resource_label(issue): issue for issue in all_issues
    }

    for result_group in result_groups:
        scanned_element = result_group.results[0].scanned_element
        name = github.compliance.issue.name_for_element(scanned_element)
        prefix = github.compliance.issue.prefix_for_element(scanned_element)

        resource_label = github.compliance.issue.digest_label(
            prefix=prefix,
            digest_str=name,
        )

        logger.info(f'Digest-Label for {result_group.name=}: {resource_label=}')
        component_resources_to_issues.pop(resource_label, None)

    # any issues that have not been removed thus far were not referenced by given result_groups
    close_issues(component_resources_to_issues.values())

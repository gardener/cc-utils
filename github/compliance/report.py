import datetime
import enum
import functools
import logging
import textwrap
import time
import typing
import urllib.parse

import cachetools
import github3.repos

import gci.componentmodel as cm
import requests

import ccc.delivery
import ci.util
import cnudie.util
import concourse.model.traits.image_scan as image_scan
import delivery.client
import delivery.model
import github.compliance.issue
import github.compliance.milestone
import github.compliance.model as gcm
import github.retry
import github.user
import model.delivery

logger = logging.getLogger(__name__)

_compliance_label_vulnerabilities = github.compliance.issue._label_bdba
_compliance_label_licenses = github.compliance.issue._label_licenses
_compliance_label_os_outdated = github.compliance.issue._label_os_outdated
_compliance_label_checkmarx = github.compliance.issue._label_checkmarx
_compliance_label_malware = github.compliance.issue._label_malware


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

    def pluralise(prefix: str, count: int):
        if count == 1:
            return prefix
        return f'{prefix}s'

    artifact_versions = ', '.join((r.version for r in artifacts))

    report_urls = '\n- '.join(report_urls)

    summary = textwrap.dedent(f'''\
        # Compliance Status Summary

        |    |    |
        | -- | -- |
        | Component | {component.name} |
        | Component-Version | {component.version} |
        | Artifact  | {artifacts[0].name} |
        | {pluralise('Artifact-Version', len(artifacts))}  | {artifact_versions} |
        | Artifact-Type | {artifact_type} |
        | {issue_description} | {issue_value} |

        The aforementioned {pluralise(artifact_type, len(artifacts))} yielded findings
        relevant for future release decisions.

        For viewing detailed scan {pluralise('report', len(artifacts))}, see the following
        {pluralise('Scan Report', len(artifacts))}:
    ''')

    return summary + '- ' + report_urls


def _template_vars(
    result_group: gcm.ScanResultGroup,
    license_cfg: image_scan.LicenseCfg,
    delivery_dashboard_url: str='',
):
    component = result_group.component
    artifact_name = result_group.artifact
    artifacts = [res.artifact for res in result_group.results]
    issue_type = result_group.issue_type

    results = result_group.results_with_findings

    artifact_versions = ', '.join((r.artifact.version for r in results))
    artifact_types = ', '.join(set(
        (
            r.artifact.type.value
            if isinstance(r.artifact.type, enum.Enum)
            else r.artifact.type
            for r in results
        )
    ))

    template_variables = {
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

    if issue_type == _compliance_label_vulnerabilities:
        analysis_results = [r.result for r in results]
        greatest_cve = max((r.greatest_cve_score for r in results))
        template_variables['summary'] = _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=greatest_cve,
            issue_description='Greatest CVE Score',
            report_urls=[ar.report_url() for ar in analysis_results],
        )
        template_variables['greatest_cve'] = greatest_cve
        template_variables['criticality_classification'] = str(_criticality_classification(
            cve_score=greatest_cve
        ))
    elif issue_type == _compliance_label_licenses:
        analysis_results = [r.result for r in results]
        prohibited_licenses = set()
        all_licenses = set()

        for r in results:
            all_licenses |= r.license_names

        for license_name in all_licenses:
            if not license_cfg.is_allowed(license_name):
                prohibited_licenses.add(license_name)

        template_variables['summary'] = _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=' ,'.join(prohibited_licenses),
            issue_description='Prohibited Licenses',
            report_urls=[ar.report_url() for ar in analysis_results],
        )
        template_variables['criticality_classification'] = str(gcm.Severity.BLOCKER)
    elif issue_type == _compliance_label_os_outdated:
        worst_result = result_group.worst_result
        os_info = worst_result.os_id

        os_name_and_version = f'{os_info.ID}:{os_info.VERSION_ID}'

        template_variables['summary'] = _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=os_name_and_version,
            issue_description='Outdated OS-Version',
            report_urls=(),
        )
    elif issue_type == _compliance_label_checkmarx:
        stat = result_group.worst_result.scan_statistic
        report_urls = [
                f'[Checkmarx Editor]({r.report_url}), [Checkmarx Summary]({r.overview_url})'
                for r in results
            ]
        summary_str = (f'Findings: High: {stat.highSeverity}, Medium: {stat.mediumSeverity}, '
            f'Low: {stat.lowSeverity}, Info: {stat.infoSeverity}')
        template_variables['summary'] = _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=summary_str,
            issue_description='Checkmarx Scan Summary',
            report_urls=report_urls,
        )
        crit = (f'Risk: {result_group.worst_result.scan_response.scanRisk}, '
            f'Risk Severity: {result_group.worst_result.scan_response.scanRiskSeverity}')
        template_variables['criticality_classification'] = crit
    elif issue_type == _compliance_label_malware:
        summary_str = ''.join((
            result.scan_result.summary() for result in results
        )).replace('\n', '')

        template_variables['summary'] = _compliance_status_summary(
            component=component,
            artifacts=artifacts,
            issue_value=summary_str,
            issue_description='ClamAV Scan Result',
            report_urls=(),
        )
        template_variables['criticality_classification'] = str(gcm.Severity.BLOCKER)
    else:
        raise NotImplementedError(issue_type)

    return template_variables


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


def _latest_processing_date(
    cve_score: float,
    max_processing_days: image_scan.MaxProcessingTimesDays,
):
    return datetime.date.today() + datetime.timedelta(
        days=max_processing_days.for_cve(cve_score=cve_score),
    )


def create_or_update_github_issues(
    result_group_collection: gcm.ScanResultGroupCollection,
    max_processing_days: image_scan.MaxProcessingTimesDays,
    gh_api: github3.GitHub=None,
    overwrite_repository: github3.repos.Repository=None,
    preserve_labels_regexes: typing.Iterable[str]=(),
    github_issue_template_cfgs: list[image_scan.GithubIssueTemplateCfg]=None,
    delivery_svc_client: delivery.client.DeliveryServiceClient=None,
    delivery_svc_endpoints: model.delivery.DeliveryEndpointsCfg=None,
    license_cfg: image_scan.LicenseCfg=None, # XXX -> callback
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

    def process_result(
        result_group: gcm.ScanResultGroup,
        action: str,
    ):
        nonlocal gh_api
        nonlocal err_count
        issue_type = result_group.issue_type

        if action == 'discard':
            results = result_group.results_without_findings
        elif action == 'report':
            results = result_group.results_with_findings

        criticality_classification = result_group.worst_severity

        if not len({r.component.name for r in results}) == 1:
            raise ValueError('not all component names are identical')

        component = result_group.component
        artifacts = [r.artifact for r in results]
        artifact = artifacts[0]

        if overwrite_repository:
            repository = overwrite_repository
        else:
            source = cnudie.util.main_source(component=component)

            if not source.access.type is cm.AccessType.GITHUB:
                raise NotImplementedError(source)

            org = source.access.org_name()
            name = source.access.repository_name()
            gh_api = ccc.github.github_api(repo_url=source.access.repoUrl)

            repository = gh_api.repository(org, name)

        known_issues = _all_issues(repository)

        if action == 'discard':
            github.compliance.issue.close_issue_if_present(
                component=component,
                artifact=artifact,
                repository=repository,
                issue_type=issue_type,
                known_issues=known_issues,
            )

            logger.info(
                f'closed (if existing) gh-issue for {component.name=} {artifact.name=} {issue_type=}'
            )
        elif action == 'report':
            if delivery_svc_client:
                try:
                    assignees = delivery.client.github_users_from_responsibles(
                        responsibles=delivery_svc_client.component_responsibles(
                            component=component,
                            artifact=artifact,
                        ),
                        github_url=repository.url,
                    )

                    assignees = tuple((
                        u.username for u in assignees
                        if github.user.is_user_active(
                            username=u.username,
                            github=gh_api,
                        )
                    ))
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 404:
                        logger.warning(f'Delivery Service returned 404 for {component.name=}, '
                            f'{artifact.name=}')
                        assignees = ()
                        target_milestone = None
                    else:
                        raise

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
            else:
                assignees = ()
                target_milestone = None

            if delivery_svc_endpoints:
                delivery_dashboard_url = _delivery_dashboard_url(
                    component=component,
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
                    component=component,
                    artifact=artifact,
                    issue_type=issue_type,
                    repository=repository,
                    body=body,
                    assignees=assignees,
                    milestone=target_milestone,
                    extra_labels=(
                        _criticality_label(classification=criticality_classification),
                    ),
                    preserve_labels_regexes=preserve_labels_regexes,
                    known_issues=known_issues,
                )
                if result_group.comment_callback:
                    def single_comment(result: gcm.ScanResult):
                        a = result.artifact
                        header = f'**{a.name}:{a.version}**\n'

                        return header + result_group.comment_callback(result)

                    def sortable_result_str(result: gcm.ScanResult):
                        c = result.component
                        a = result.artifact
                        return f'{c.name}:{c.version}/{a.name}:{a.version}'

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

                logger.info(
                    f'updated gh-issue for {component.name=} {artifact.name=} '
                    f'{issue_type=}: {issue.html_url=}'
                )
            except github3.exceptions.GitHubError as ghe:
                err_count += 1
                logger.warning('error whilst trying to create or update issue - will keep going')
                logger.warning(f'error: {ghe} {ghe.code=} {ghe.message=}')

        else:
            raise NotImplementedError(action)

    for result_group in result_groups_with_findings:
        process_result(
            result_group=result_group,
            action='report',
        )
        time.sleep(1) # throttle github-api-requests

    for result_group in result_groups_without_findings:
        logger.info(f'discarding issue for {result_group.name=} vulnerabilities')
        process_result(
            result_group=result_group,
            action='discard',
        )
        time.sleep(1) # throttle github-api-requests

    if groups_with_scan_error := result_group_collection.result_groups_with_scan_errors:
        logger.warning(f'{len(groups_with_scan_error)=} had scanning errors (check log)')
        # do not fail job (for now); might choose to, later

    if overwrite_repository:
        known_issues = _all_issues(overwrite_repository)
        close_issues_for_absent_resources(
            result_groups=result_groups,
            known_issues=known_issues,
            issue_type=result_group_collection.issue_type,
        )

    if err_count > 0:
        logger.warning(f'{err_count=} - there were errors - will raise')
        raise ValueError('not all gh-issues could be created/updated/deleted')


def close_issues_for_absent_resources(
    result_groups: list[gcm.ScanResultGroup],
    known_issues: typing.Iterator[github3.issues.issue.ShortIssue],
    issue_type: str | None,
):
    '''
    closes all open issues for component-resources that are not present in given result-groups.

    this is intended to automatically close issues for components or component-resources that
    have been removed from BoM.
    '''
    all_issues = github.compliance.issue.enumerate_issues(
        component=None,
        artifact=None,
        issue_type=issue_type,
        known_issues=known_issues,
        state='open',
    )

    def component_resource_label(issue: github3.issues.Issue) -> str:
        for label in issue.labels():
            if label.name.startswith('ocm/resource'):
                return label.name

    component_resources_to_issues = {
        component_resource_label(issue): issue for issue in all_issues
    }

    for result_group in result_groups:
        resource_label = github.compliance.issue.artifact_digest_label(
            component=result_group.component,
            artifact=result_group.artifact,
        )
        logger.info(f'Digest-Label for {result_group.name=}: {resource_label=}')
        component_resources_to_issues.pop(resource_label, None)

    # any issues that have not been removed thus far were not referenced by given result_groups
    for issue in component_resources_to_issues.values():
        logger.info(
            f"Closing issue '{issue.title}'({issue.html_url}) since no scan contained a resource "
            "matching its digest."
        )
        issue.create_comment('closing, because component/resource no longer present in BoM')
        issue.close()

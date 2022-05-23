# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import collections
import dataclasses
import datetime
import enum
import functools
import json
import logging
import tempfile
import textwrap
import typing
import urllib.parse

import github3.exceptions
import tabulate

import gci.componentmodel as cm

import ccc.concourse
import ccc.delivery
import ccc.github
import ci.util
import cnudie.util
import concourse.model.traits.image_scan as image_scan
import concourse.util
import delivery.client
import delivery.model
import github.compliance.issue
import github.compliance.milestone
import mailutil
import model.delivery
import reutil
import saf.model
import protecode.model as pm

from concourse.model.traits.image_scan import Notify

logger = logging.getLogger()

# monkeypatch: disable html escaping
tabulate.htmlescape = lambda x: x


def _target_sprint(
    delivery_svc_client: delivery.client.DeliveryServiceClient,
):
    today = datetime.date.today()

    issue_freeze_offset = datetime.timedelta(days=-7)

    current_sprint = delivery_svc_client.sprint_current()
    issue_freeze_date =  current_sprint.release_decision + issue_freeze_offset

    if today < issue_freeze_date:
        return current_sprint

    # issues that are found "shortly" before release-decision are assigned to next sprint's milestone
    next_sprint = delivery_svc_client.sprint_current(offset=+1)

    return next_sprint


@functools.cache
def _target_milestone(
    repo: github3.repos.Repository,
    sprint: delivery.model.Sprint,
):
    return github.compliance.milestone.find_or_create_sprint_milestone(
        repo=repo,
        sprint=sprint,
    )


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


def _criticality_classification(cve_score: float):
    if not cve_score or cve_score <= 0:
        return None

    if cve_score < 4.0:
        return 'low'
    if cve_score < 7.0:
        return 'medium'
    if cve_score < 9.0:
        return 'high'
    if cve_score >= 9.0:
        return 'critical'


def _criticality_label(classification: str):
    return f'compliance-priority/{classification}'


def _compliance_status_summary(
    component: cm.Component,
    resources: cm.Resource,
    greatest_cve: str,
    report_urls: str,
):
    if isinstance(resources[0].type, enum.Enum):
        resource_type = resources[0].type.value
    else:
        resource_type = resources[0].type

    def pluralise(prefix: str, count: int):
        if count == 1:
            return prefix
        return f'{prefix}s'

    resource_versions = ', '.join((r.version for r in resources))

    report_urls = '\n- '.join(report_urls)

    summary = textwrap.dedent(f'''\
        # Compliance Status Summary

        |    |    |
        | -- | -- |
        | Component | {component.name} |
        | Component-Version | {component.version} |
        | Resource  | {resources[0].name} |
        | {pluralise('Resource-Version', len(resources))}  | {resource_versions} |
        | Resource-Type | {resource_type} |
        | Greatest CVSSv3 Score | **{greatest_cve}** |

        The aforementioned {pluralise(resource_type, len(resources))}, declared
        by the given content was found to contain potentially relevant vulnerabilities.

        For viewing detailed scan {pluralise('report', len(resources))}, see the following
        {pluralise('Scan Report', len(resources))}:
    ''')

    return summary + '- ' + report_urls


def create_or_update_github_issues(
    results_to_report: typing.Sequence[pm.BDBA_ScanResult],
    results_to_discard: typing.Sequence[pm.BDBA_ScanResult],
    preserve_labels_regexes: typing.Iterable[str],
    issue_tgt_repo_url: str=None,
    github_issue_template_cfg: image_scan.GithubIssueTemplateCfg=None,
    delivery_svc_endpoints: model.delivery.DeliveryEndpointsCfg=None,
):
    logger.info(f'{len(results_to_report)=}, {len(results_to_discard)=}')

    if issue_tgt_repo_url:
        gh_api = ccc.github.github_api(repo_url=issue_tgt_repo_url)

        if not '://' in issue_tgt_repo_url:
            issue_tgt_repo_url = 'x://' + issue_tgt_repo_url

        org, name = urllib.parse.urlparse(issue_tgt_repo_url).path.strip('/').split('/')
        overwrite_repository = gh_api.repository(org, name)
    else:
        overwrite_repository = None

    if delivery_svc_endpoints:
        delivery_svc_client = delivery.client.DeliveryServiceClient(
            routes=delivery.client.DeliveryServiceRoutes(
                base_url=delivery_svc_endpoints.base_url(),
            )
        )
    else:
        delivery_svc_client = ccc.delivery.default_client_if_available()

    if delivery_svc_client:
        target_sprint = _target_sprint(delivery_svc_client=delivery_svc_client)
    else:
        target_sprint = None

    # workaround / hack:
    # we map findings to <component-name>:<resource-name>
    # in case of ambiguities, this would lead to the same ticket firstly be created, then closed
    # -> do not close tickets in this case.
    # a cleaner approach would be to create seperate tickets, or combine findings into shared
    # tickets. For the time being, this should be "good enough"
    def to_component_resource_name(result):
        return f'{result.component.name}:{result.resource.name}'

    reported_component_resource_names = {
        to_component_resource_name(result) for result in results_to_report
    }

    results_to_discard = [
        result for result in results_to_discard
        if to_component_resource_name(result) not in reported_component_resource_names
    ]

    grouped_results_to_report = collections.defaultdict(list)
    for result in results_to_report:
        grouped_results_to_report[to_component_resource_name(result)].append(result)

    err_count = 0

    def process_result(results: pm.BDBA_ScanResult, action:str):
        nonlocal gh_api
        nonlocal err_count

        greatest_cve = max(results, key=lambda r: r.greatest_cve_score).greatest_cve_score

        if not len({r.component.name for r in results}) == 1:
            raise ValueError('not all component names are identical')

        component = results[0].component
        resources = [r.resource for r in results]
        resource = resources[0]
        analysis_results = [r.result for r in results]
        analysis_res = analysis_results[0]

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

        if action == 'discard':
            github.compliance.issue.close_issue_if_present(
                component=component,
                resource=resource,
                repository=repository,
            )
            logger.info(f'closed (if existing) gh-issue for {component.name=} {resource.name=}')
        elif action == 'report':
            if delivery_svc_client:
                assignees = delivery.client.github_users_from_responsibles(
                    responsibles=delivery_svc_client.component_responsibles(
                        component=component,
                        resource=resource,
                    ),
                    github_url=repository.url,
                )

                def user_is_active(username):
                    try:
                        user = gh_api.user(username)
                        if user.as_dict().get('suspended_at'):
                            return False
                        return True
                    except github3.exceptions.NotFoundError:
                        logger.warning(f'{username=} not found')
                        return False

                assignees = tuple((u.username for u in assignees if user_is_active(u.username)))

                try:
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

            if isinstance(resource.type, enum.Enum):
                resource_type = resource.type.value
            else:
                resource_type = resource.type

            if delivery_svc_endpoints:
                delivery_dashboard_url = _delivery_dashboard_url(
                    component=component,
                    base_url=delivery_svc_endpoints.dashboard_url(),
                )
                delivery_dashboard_url = f'[Delivery-Dashboard]({delivery_dashboard_url})'
            else:
                delivery_dashboard_url = ''

            criticality_classification = _criticality_classification(cve_score=greatest_cve)

            template_variables = {
                'summary': _compliance_status_summary(
                    component=component,
                    resources=resources,
                    greatest_cve=greatest_cve,
                    report_urls=[ar.report_url() for ar in analysis_results],
                ),
                'component_name': component.name,
                'component_version': component.version,
                'resource_name': resource.name,
                'resource_version': resource.version,
                'resource_type': resource_type,
                'greatest_cve': greatest_cve,
                'criticality_classification': criticality_classification,
                'bdba_report_url': analysis_res.report_url(),
                'report_url': analysis_res.report_url(),
                'delivery_dashboard_url': delivery_dashboard_url,
            }

            if github_issue_template_cfg and (body := github_issue_template_cfg.body):
                body = body.format(**template_variables)
            else:
                body = textwrap.dedent(f'''\
                    # Compliance Status Summary

                    |    |    |
                    | -- | -- |
                    | Component | {component.name} |
                    | Component-Version | {component.version} |
                    | Resource  | {resource.name} |
                    | Resource-Version  | {resource.version} |
                    | Resource-Type | {resource_type} |
                    | Greatest CVSSv3 Score | **{greatest_cve}** |

                    The aforementioned {resource_type}, declared by the given content was found to
                    contain potentially relevant vulnerabilities.

                    See [scan report]({analysis_res.report_url()}) for both viewing a detailed
                    scanning report, and doing assessments (see below).

                    **Action Item**

                    Please take appropriate action. Choose either of:

                    - assess findings
                    - upgrade {resource_type} version
                    - minimise image

                    In case of systematic false-positives, consider adding scanning-hints to your
                    Component-Descriptor.
                '''
                )

            try:
                github.compliance.issue.create_or_update_issue(
                    component=component,
                    resource=resource,
                    repository=repository,
                    body=body,
                    assignees=assignees,
                    milestone=target_milestone,
                    extra_labels=(
                        _criticality_label(classification=criticality_classification),
                    ),
                    preserve_labels_regexes=preserve_labels_regexes,
                )
            except github3.exceptions.GitHubError as ghe:
                err_count += 1
                logger.warning('error whilst trying to create or update issue - will keep going')
                logger.warning(f'error: {ghe} {ghe.code=} {ghe.message()=}')

            logger.info(f'updated gh-issue for {component.name=} {resource.name=}')
        else:
            raise NotImplementedError(action)

    for results in grouped_results_to_report.values():
        process_result(results=results, action='report')

    for result in results_to_discard:
        process_result(results=(result,), action='discard')

    if err_count > 0:
        logger.warning(f'{err_count=} - there were errors - will raise')
        raise ValueError('not all gh-issues could be created/updated/deleted')


class MailRecipients:
    def __init__(
        self,
        root_component_name: str,
        cfg_set,
        protecode_cfg: None,
        protecode_group_id: int=None,
        protecode_group_url: str=None,
        cvss_version: pm.CVSSVersion=None,
        result_filter=None,
        recipients: typing.List[str]=[],
        recipients_component: cm.Component=None,
    ):
        self._root_component_name = root_component_name
        self._result_filter = result_filter

        self._protecode_results = []
        self._protecode_results_below_threshold = []
        self._license_scan_results = []
        self._clamav_results = None

        self._cfg_set = cfg_set
        if not bool(recipients) ^ bool(recipients_component):
            raise ValueError('exactly one of recipients, component_name must be given')
        self._recipients = recipients
        self._recipients_component = recipients_component
        self._protecode_cfg = protecode_cfg
        self._protecode_group_id = protecode_group_id
        self._protecode_group_url = protecode_group_url
        self._cvss_version = cvss_version

    @functools.lru_cache()
    def resolve_recipients(self):
        if not self._recipients_component:
            return self._recipients

        # XXX it should not be necessary to pass github_cfg
        return mailutil.determine_mail_recipients(
            github_cfg_name=self._cfg_set.github().name(),
            components=(self._recipients_component,),
        )

    def add_protecode_results(
        self,
        relevant_results: typing.Sequence[pm.BDBA_ScanResult],
        results_below_threshold: typing.Sequence[pm.BDBA_ScanResult],
    ):
        logger.info(f'adding protecode results for {self}')

        self._protecode_results.extend([
                r for r in relevant_results
                if not self._result_filter or self._result_filter(component=r.component)
            ])

        self._protecode_results_below_threshold.extend([
                r for r in results_below_threshold
                if not self._result_filter or self._result_filter(component=r.component)
            ])

    def add_license_scan_results(
        self,
        results: typing.Iterable[
            tuple[
                typing.Tuple[pm.BDBA_ScanResult],
                typing.Iterable[pm.License],
                typing.Iterable[pm.License],
            ],
        ],
    ):
        logger.info(f'adding license scan results for {self}')
        self._license_scan_results.extend([
                r for r in results
                if not self._result_filter or self._result_filter(component=r[0])
            ])

    def add_clamav_results(self, results: saf.model.MalwarescanResult):
        if self._clamav_results is None:
            self._clamav_results = []

        for result in results:
            self._clamav_results.append(result)

    def has_results(self):
        return any([
            self._protecode_results,
            self._clamav_results,
            self._license_scan_results,
        ])

    def mail_body(self):
        parts = []
        parts.append(self._mail_disclaimer())

        if self._protecode_results:
            parts.append(self._protecode_report())
            if self._protecode_results_below_threshold:
                parts.append(self._results_below_threshold_report())
        if self._license_scan_results:
            parts.append(self._license_report())
        if self._clamav_results is not None:
            parts.append(self._clamav_report())

        return ''.join(parts)

    def _mail_disclaimer(self):
        return textwrap.dedent(f'''
            <div>
              <p>
              Note: you receive this E-Mail, because you were configured as a mail recipient
              in repository "{self._root_component_name}" (see .ci/pipeline_definitions)
              To remove yourself, search for your e-mail address in said file and remove it.
              </p>
              <p>
              You can find the Concourse job that generated this e-mail
              <a href="{concourse.util.own_running_build_url()}">here</a>.
              </p>
            </div>
        ''')

    def _protecode_report(self):
        result = textwrap.dedent(f'''
            <p>
              The following components in Protecode-group
              <a href="{self._protecode_group_url}">{self._protecode_group_id}</a>
              were found to contain critical vulnerabilities (according to
              {self._cvss_version.value}):
            </p>
        ''')
        return result + protecode_results_table(
            protecode_cfg=self._protecode_cfg,
            upload_results=self._protecode_results,
            show_cve=True,
        )

    def _results_below_threshold_report(self):
        result = textwrap.dedent(f'''
            <p>
              For your overview, the following components
              have vulnerabilites below the threshold (according to {self._cvss_version.value}):
            </p>
        ''')
        return result + protecode_results_table(
            protecode_cfg=self._protecode_cfg,
            upload_results=self._protecode_results_below_threshold,
            show_cve=False,
        )

    def _license_report(self):
        result = textwrap.dedent(f'''
            <p>
              The following components in Protecode-group
              <a href="{self._protecode_group_url}">{self._protecode_group_id}</a>
              have licenses to review. Licenses are separated in rejected licenses (explicitly
              configured to be rejected) and unclassified licenses (neither explicitly accepted
              nor explicitly prohibited):
            </p>
        ''')
        return result + license_scan_results_table(
            protecode_cfg=self._protecode_cfg,
            license_report=self._license_scan_results,
        )

    def _clamav_findings_to_str(self, scan_result):
        if scan_result.findings:
            return '\n'.join(scan_result.findings)
        else:
            return 'No findings'

    def _clamav_report(self):
        result = '<p><div>Virus Scanning Results:</div>'
        return result + tabulate.tabulate(
            map(
                lambda sr: (
                    sr.resource.access.imageReference,
                    sr.scan_state,
                    self._clamav_findings_to_str(sr)
                ),
                self._clamav_results,
            ),
            headers=('Resource Name', 'Scan State', 'Findings'),
            tablefmt='html',
        )

    def __repr__(self):
        if self._recipients_component:
            descr = f'component {self._recipients_component.name}'
        else:
            descr = 'for all results'

        return 'MailRecipients: ' + descr


def mail_recipients(
    notification_policy: Notify,
    root_component_name:str,
    cfg_set,
    protecode_cfg=None,
    protecode_group_id: int=None,
    protecode_group_url: str=None,
    cvss_version: pm.CVSSVersion=None,
    email_recipients: typing.Iterable[str]=(),
    components: typing.Iterable[cm.Component]=(),
):
    mail_recps_ctor = functools.partial(
        MailRecipients,
        root_component_name=root_component_name,
        protecode_cfg=protecode_cfg,
        protecode_group_id=protecode_group_id,
        protecode_group_url=protecode_group_url,
        cvss_version=cvss_version,
        cfg_set=cfg_set,
    )

    notification_policy = Notify(notification_policy)
    if notification_policy == Notify.EMAIL_RECIPIENTS:
        if not email_recipients:
            raise ValueError('at least one email_recipient must be specified')

        # exactly one MailRecipients, catching all (hence no filter)
        yield mail_recps_ctor(
            recipients=email_recipients,
        )
    elif notification_policy == Notify.NOBODY:
        return
    elif notification_policy == Notify.COMPONENT_OWNERS:
        def make_comp_filter(own_component):
            def comp_filter(component):
                return own_component.name == component.name # only care about matching results
            return comp_filter

        for comp in components:
            yield mail_recps_ctor(
                recipients_component=comp,
                result_filter=make_comp_filter(own_component=comp)
            )
    else:
        raise NotImplementedError()


def protecode_results_table(
    protecode_cfg,
    upload_results: typing.Iterable[pm.BDBA_ScanResult],
    show_cve: bool=True,
):
    def result_to_tuple(upload_result: pm.BDBA_ScanResult):
        greatest_cve = upload_result.greatest_cve_score
        # protecode.model.AnalysisResult
        analysis_result = upload_result.result

        name = analysis_result.display_name()
        analysis_url = \
            f'{protecode_cfg.api_url()}/products/{analysis_result.product_id()}/#/analysis'
        link_to_analysis_url = f'<a href="{analysis_url}">{name}</a>'

        custom_data = analysis_result.custom_data()
        if custom_data is not None:
          image_reference = custom_data.get('IMAGE_REFERENCE')
          image_reference_url = f'<a href="https://{image_reference}">{image_reference}</a>'
        else:
          image_reference_url = None

        if show_cve:
            return (link_to_analysis_url, greatest_cve, image_reference_url)
        else:
            return (link_to_analysis_url, image_reference_url)

    if show_cve:
        table_headers = ('Component Name', 'Greatest CVE', 'Container Image Reference')
    else:
        table_headers = ('Component Name', 'Container Image Reference')

    for r in upload_results:
        print(str(r))

    table = tabulate.tabulate(
      map(result_to_tuple, upload_results),
      headers=table_headers,
      tablefmt='html',
    )
    return table


def license_scan_results_table(license_report, protecode_cfg):
    def license_scan_report_to_rows(license_report):
        for upload_result, rejected_licenses, unclassified_licenses in license_report:
            analysis_result = upload_result.result

            name = analysis_result.display_name()
            analysis_url = \
                f'{protecode_cfg.api_url()}/products/{analysis_result.product_id()}/#/analysis'
            link_to_analysis_url = f'<a href="{analysis_url}">{name}</a>'
            rejected_licenses_str = ', '.join([l.name() for l in rejected_licenses])
            unclassified_licenses_str = ', '.join([l.name() for l in unclassified_licenses])

            yield [link_to_analysis_url, rejected_licenses_str, unclassified_licenses_str]

    table = tabulate.tabulate(
        license_scan_report_to_rows(license_report),
        headers=('Component Name', 'Rejected Licenses', 'Unclassified Licenses'),
        tablefmt='html',
    )
    return table


def print_license_report(license_report):
    def to_table_row(upload_result, licenses):
        component_name = upload_result.result.display_name()
        license_names = {license.name() for license in licenses}
        license_names_str = ', '.join(license_names)
        yield (component_name, license_names_str)

    license_lines = [
        to_table_row(upload_result, licenses)
        for upload_result, licenses in license_report
    ]
    print(tabulate.tabulate(
        license_lines,
        headers=('Component Name', 'Licenses'),
        )
    )

    return license_lines


def determine_rejected_licenses(license_report, allowed_licenses, prohibited_licenses):
    accepted_filter_func = reutil.re_filter(
        include_regexes=allowed_licenses,
        exclude_regexes=prohibited_licenses,
    )

    prohibited_filter_func = reutil.re_filter(
        include_regexes=prohibited_licenses,
    )

    for upload_result, licenses in license_report:
        all_licenses = set(licenses)

        accepted_licenses = {l for l in all_licenses if accepted_filter_func(l.name())}

        # The filter will always return true if its 'prohibited_licenses' is an empty collection.
        if prohibited_licenses:
            rejected_licenses = {l for l in all_licenses if prohibited_filter_func(l.name())}
        else:
            rejected_licenses = set()

        unclassified_licenses = all_licenses - (accepted_licenses | rejected_licenses)

        if rejected_licenses or unclassified_licenses:
            yield upload_result, rejected_licenses, unclassified_licenses


def print_protecode_info_table(
    protecode_group_url: str,
    protecode_group_id: int,
    reference_protecode_group_ids: typing.List[int],
    cvss_version: pm.CVSSVersion,
    include_image_references: typing.List[str],
    exclude_image_references: typing.List[str],
    include_image_names: typing.List[str],
    exclude_image_names: typing.List[str],
    include_component_names: typing.List[str],
    exclude_component_names: typing.List[str],
):
    headers = ('Protecode Scan Configuration', '')
    entries = (
        ('Protecode target group id', str(protecode_group_id)),
        ('Protecode group URL', protecode_group_url),
        ('Protecode reference group IDs', reference_protecode_group_ids),
        ('Used CVSS version', cvss_version.value),
        ('Image reference filter (include)', include_image_references),
        ('Image reference filter (exclude)', exclude_image_references),
        ('Image name filter (include)', include_image_names),
        ('Image name filter (exclude)', exclude_image_names),
        ('Component name filter (include)', include_component_names),
        ('Component name filter (exclude)', exclude_component_names),
    )
    print(tabulate.tabulate(entries, headers=headers))


def retrieve_buildlog(uuid: str):
    concourse_cfg = concourse.util._current_concourse_config()

    pipeline_metadata = concourse.util.get_pipeline_metadata()
    client = ccc.concourse.client_from_cfg_name(
        concourse_cfg_name=concourse_cfg.name(),
        team_name=pipeline_metadata.team_name,
    )
    build = concourse.util.find_own_running_build()
    build_id = build.id()
    task_id = client.build_plan(build_id=build_id).task_id(task_name='malware-scan')
    build_events = client.build_events(build_id=build_id)

    log = ''
    for line in build_events.iter_buildlog(task_id=task_id):
        log += f'{line}'
        if uuid in line:
            break
    return log


class EnumJSONEncoder(json.JSONEncoder):
    '''
    a json.JSONEncoder that will encode enum objects using their values
    '''
    def default(self, o):
        if isinstance(o, enum.Enum):
            return o.value
        return super().default(o)


def dump_malware_scan_request(request: saf.model.EvidenceRequest):
    request_dict = dataclasses.asdict(request)
    with tempfile.NamedTemporaryFile(delete=False, mode='wt') as tmp_file:
        tmp_file.write(json.dumps(request_dict, cls=EnumJSONEncoder))

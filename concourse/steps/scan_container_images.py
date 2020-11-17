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

import dataclasses
import functools
import logging
import textwrap
import typing

import requests.exceptions
import tabulate

import ccc.clamav
import ci.util
import concourse.util
import mailutil
import reutil

from concourse.model.traits.image_scan import Notify
from product.model import ComponentName, UploadResult
from protecode.model import CVSSVersion, License

logger = logging.getLogger()

# monkeypatch: disable html escaping
tabulate.htmlescape = lambda x: x


@dataclasses.dataclass
class MalwareScanResult:
    image_reference: str
    file_path: str
    finding: str

    @staticmethod
    def headers():
        ''' Return a list of headers to be used when rendering this dataclass
        using tabular.tabulate()
        '''
        return ('Image Reference', 'File Path', 'Scan Result')


class MailRecipients:
    def __init__(
        self,
        root_component_name: str,
        cfg_set,
        protecode_cfg: None,
        protecode_group_id: int=None,
        protecode_group_url: str=None,
        cvss_version: CVSSVersion=None,
        result_filter=None,
        recipients: typing.List[str]=[],
        recipients_component: ComponentName=None,
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
            component_names=(self._recipients_component.name(),),
        )

    def add_protecode_results(
        self,
        relevant_results: typing.Iterable[typing.Tuple[UploadResult, float]],
        results_below_threshold: typing.Iterable[typing.Tuple[UploadResult, float]],
    ):
        logger.info(f'adding protecode results for {self}')

        self._protecode_results.extend([
                r for r in relevant_results
                if not self._result_filter or self._result_filter(component=r[0].component)
            ])

        self._protecode_results_below_threshold.extend([
                r for r in results_below_threshold
                if not self._result_filter or self._result_filter(component=r[0].component)
            ])

    def add_license_scan_results(
        self,
        results: typing.Iterable[
            typing.Tuple[UploadResult, typing.Iterable[License], typing.Iterable[License]]
        ],
    ):
        logger.info(f'adding license scan results for {self}')
        self._license_scan_results.extend([
                r for r in results
                if not self._result_filter or self._result_filter(component=r[0])
            ])

    def add_clamav_results(self, results: MalwareScanResult):
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

    def _clamav_report(self):
        result = '<p><div>Virus Scanning Results:</div>'
        return result + tabulate.tabulate(
            map(lambda dc: dataclasses.astuple(dc), self._clamav_results),
            headers=MalwareScanResult.headers(),
            tablefmt='html',
        )

    def __repr__(self):
        if self._recipients_component:
            descr = f'component {self._recipients_component.name()}'
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
    cvss_version: CVSSVersion=None,
    email_recipients: typing.Iterable[str]=(),
    components: typing.Iterable[ComponentName]=(),
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
                print(f'filter: component: {own_component.name} - other: {component.name}')
                return own_component.name == component.name # only care about matching results
            return comp_filter

        for comp in components:
            yield mail_recps_ctor(
                recipients_component=comp,
                result_filter=make_comp_filter(own_component=comp)
            )
    else:
        raise NotImplementedError()


def virus_scan_images(image_references: typing.Iterable[str], clamav_config_name: str):
    clamav_client = ccc.clamav.client_from_config_name(clamav_config_name)
    for image_reference in image_references:
        try:
            scan_results = [
                MalwareScanResult(
                    image_reference=image_reference,
                    file_path=path.split(':')[1],
                    finding=scan_result.virus_signature(),
                )
                for scan_result, path in clamav_client.scan_container_image(
                    image_reference=image_reference
                )
            ]
            if scan_results:
                yield from scan_results
            else:
                yield MalwareScanResult(
                    image_reference=image_reference,
                    file_path='-',
                    finding='No malware detected.',
                )
        except requests.exceptions.RequestException as e:
            ci.util.warning(
                f'A connection error occurred while scanning the image "{image_reference}" '
                f'for viruses: {e}'
            )
            yield MalwareScanResult(
                image_reference=image_reference,
                file_path='-',
                finding=f'A connection error occurred while scanning the image: {e}'
            )


def protecode_results_table(
    protecode_cfg,
    upload_results: typing.Iterable[UploadResult],
    show_cve: bool=True,
):
    def result_to_tuple(upload_result: UploadResult):
        # upload_result tuple of product.model.UploadResult and CVE Score
        upload_result, greatest_cve = upload_result
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
            return [link_to_analysis_url, greatest_cve, image_reference_url]
        else:
            return [link_to_analysis_url, image_reference_url]

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
    cvss_version: CVSSVersion,
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

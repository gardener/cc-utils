# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import functools
import textwrap
import typing

import tabulate

import clamav.util
import mailutil

from concourse.model.traits.image_scan import Notify
from product.model import ComponentName, UploadResult


class MailRecipients(object):
    def __init__(
        self,
        root_component_name: str,
        protecode_cfg,
        protecode_group_id: int,
        protecode_group_url: str,
        cfg_set,
        result_filter=None,
        recipients: typing.List[str]=[],
        recipients_component: ComponentName=None,
    ):
        self._root_component_name = root_component_name
        self._result_filter = result_filter
        self._protecode_results = []
        self._clamav_results = []
        self._cfg_set = cfg_set
        if not bool(recipients) ^ bool(recipients_component):
            raise ValueError('exactly one of recipients, component_name must be given')
        self._recipients = recipients
        self._recipients_component= recipients_component
        self._protecode_cfg = protecode_cfg
        self._protecode_group_id = protecode_group_id
        self._protecode_group_url = protecode_group_url

    @functools.lru_cache()
    def resolve_recipients(self):
        if not self._recipients_component:
            return self._recipients

        # XXX it should not be necessary to pass github_cfg
        return mailutil.determine_mail_recipients(
            github_cfg_name=self._cfg_set.github().name(),
            component_names=(self._recipients_component.name(),),
        )

    def add_protecode_results(self, results: typing.Iterable[typing.Tuple[UploadResult, int]]):
        print(f'adding protecode results for {self}')
        for result in results:
            if self._result_filter:
                if not self._result_filter(component=result[0].component):
                    print(f'did not match: {result[0].component.name()}')
                    continue
            self._protecode_results.append(result)

    def add_clamav_results(self, results):
        for result in results:
            self._clamav_results.append(result)

    def has_results(self):
        if self._protecode_results:
            return True
        if self._clamav_results:
            return True

    def mail_body(self):
        parts = []
        parts.append(self._mail_disclaimer())
        parts.append(protecode_results_table(
            protecode_cfg=self._protecode_cfg,
            upload_results=self._protecode_results,
            )
        )
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
              The following components in Protecode-group
              <a href="{self._protecode_group_url}">{self._protecode_group_id}</a>
              were found to contain critical vulnerabilities:
              </p>
            </div>
          ''')

    def _clamav_report(self):
        if not self._clamav_results:
            return textwrap.dedent(f'''
                <p>Scanned all container image(s) for matching virus signatures
                without any matches (id est: all container images seem to be free of known malware)
            ''')
        result = '<p><div>Virus Scanning Results</div>'
        return result + tabulate.tabulate(
            self._clamav_results,
            headers=('Image-Reference', 'Scanning Result'),
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
    protecode_cfg,
    protecode_group_id: int,
    protecode_group_url: str,
    cfg_set,
    email_recipients: typing.Iterable[str]=(),
    components: typing.Iterable[ComponentName]=(),
):
    mail_recps_ctor = functools.partial(
        MailRecipients,
        root_component_name=root_component_name,
        protecode_cfg=protecode_cfg,
        protecode_group_id=protecode_group_id,
        protecode_group_url=protecode_group_url,
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
                print(f'filter: component: {own_component.name()} - other: {component.name()}')
                return own_component.name() == component.name() # only care about matching results
            return comp_filter

        for comp in components:
            yield mail_recps_ctor(
                recipients_component=comp,
                result_filter=make_comp_filter(own_component=comp)
            )
    else:
        raise NotImplementedError()


def virus_scan_images(image_references: typing.Iterable[str]):
    for image_reference in image_references:
        status, signature = clamav.util.scan_container_image(image_reference=image_reference)
        if clamav.util.result_ok(status=status, signature=signature):
            continue
        yield (image_reference, f'{status}: {signature}')


def protecode_results_table(protecode_cfg, upload_results: typing.Iterable[UploadResult]):
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
        else:
          image_reference = None

        return [link_to_analysis_url, greatest_cve, image_reference]

    table = tabulate.tabulate(
      map(result_to_tuple, upload_results),
      headers=('Component Name', 'Greatest CVE', 'Container Image Reference'),
      tablefmt='html',
    )
    return table


def create_license_report(license_report):
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

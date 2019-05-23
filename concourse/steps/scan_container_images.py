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

import textwrap
import typing

import tabulate

import clamav.util

from product.model import ComponentName, UploadResult


class MailRecipients(object):
    def __init__(
        self,
        root_component_name: str,
        protecode_cfg,
        protecode_group_id: int,
        protecode_group_url: str,
        result_filter=None,
        recipients: typing.List[str]=[],
        recipients_component_name: ComponentName=None,
    ):
        self._root_component_name = root_component_name
        self._result_filter = result_filter
        self._protecode_results = []
        self._clamav_results = []
        if not bool(recipients) ^ bool(recipients_component_name):
            raise ValueError('exactly one of recipients, component_name must be given')
        self._recipients = recipients
        self._recipients_component_name = recipients_component_name
        self._protecode_cfg = protecode_cfg
        self._protecode_group_id = protecode_group_id
        self._protecode_group_url = protecode_group_url

    def resolve_recipients(self):
        if not self._recipients_component_name:
            return self._recipients
        raise NotImplementedError()

    def add_protecode_results(self, results: typing.Iterable[typing.Tuple[UploadResult, int]]):
        for result in results:
            if self._result_filter:
                if self._result_filter(component=result[0].component):
                    continue
            self._protecode_results.append(result)

    def add_clamav_results(self, results):
        for result in results:
            self._clamav_results.append(result)

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

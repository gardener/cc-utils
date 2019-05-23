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

import typing

import tabulate

import clamav.util

from product.model import ComponentName, UploadResult


class MailRecipients(object):
    def __init__(
        self,
        result_filter=lambda _:True,
        recipients: typing.List[str]=[],
        component_name: ComponentName=None,
    ):
        self._result_filter = result_filter
        self._protecode_results = []
        if not bool(recipients) ^ bool(component_name):
            raise ValueError('exactly one of recipients, component_name must be given')
        self._recipients = recipients
        self._component_name = component_name

    def resolve_recipients(self):
        if not self._component_name:
            return self._recipients

    def add_protecode_results(self, results: typing.Iterable[UploadResult]):
        for result in results:
            if not self._result_filter(component=result.component):
                continue
            self._protecode_results.append(result)


def virus_scan_images(image_references: typing.Iterable[str]):
    for image_reference in image_references:
        status, signature = clamav.util.scan_container_image(image_reference=image_reference)
        if clamav.util.result_ok(status=status, signature=signature):
            continue
        yield (image_reference, f'{status}: {signature}')


def protecode_results_table(protecode_cfg, upload_results: typing.Iterable[UploadResult]):
    def result_to_tuple(upload_result: UploadResult):
        # upload_result tuple of product.model.UploadResult and CVE Score
        upload_result = upload_result[0]
        # protecode.model.AnalysisResult
        analysis_result = upload_result.result
        greatest_cve = upload_result[1]

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

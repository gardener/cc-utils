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

from enum import Enum
from typing import Iterable

import ci.util
from model.base import ModelBase


class VersionOverrideScope(Enum):
    APP = 1
    GROUP = 2
    GLOBAL = 3


class ProcessingStatus(Enum):
    BUSY = 'B'
    READY = 'R'
    FAILED = 'F'


class CVSSVersion(Enum):
    V2 = 'CVSSv2'
    V3 = 'CVSSv3'


class AnalysisResult(ModelBase):
    def product_id(self):
        return self.raw.get('product_id')

    def display_name(self):
        return self.raw.get('filename', '<None>')

    def name(self):
        return self.raw.get('name')

    def status(self) -> ProcessingStatus:
        return ProcessingStatus(self.raw.get('status'))

    def components(self) -> 'Iterable[Component]':
        return (Component(raw_dict=raw) for raw in self.raw.get('components', []))

    def custom_data(self):
        return self.raw.get('custom_data')

    def __repr__(self):
        return f'{self.__class__.__name__}: {self.display_name()}({self.product_id()})'


class Component(ModelBase):
    def name(self):
        return self.raw.get('lib')

    def version(self):
        return self.raw.get('version')

    def vulnerabilities(self) -> 'Iterable[Vulnerability]':
        return (Vulnerability(raw_dict=raw) for raw in self.raw.get('vulns'))

    def license(self) -> 'License':
        license_raw = self.raw.get('license', None)
        if not license_raw:
            return None
        return License(raw_dict=license_raw)

    def extended_objects(self) -> 'Iterable[ExtendedObject]':
        return (ExtendedObject(raw_dict=raw) for raw in self.raw.get('extended-objects'))

    def __repr__(self):
        return (
            f'{self.__class__.__name__}: {self.name()} '
            f'{self.version() or "Version not detected"}'
        )


class ExtendedObject(ModelBase):
    def name(self):
        return self.raw.get('name')

    def sha1(self):
        return self.raw.get('sha1')


class License(ModelBase):
    def name(self):
        return self.raw.get('name')

    def license_type(self):
        return self.raw.get('type')

    def url(self):
        return self.raw.get('url')

    def __eq__(self, other):
        if not isinstance(other, License):
            return False

        return self.name() == other.name() \
            and self.license_type() == other.license_type() \
            and self.url() == other.url()

    def __hash__(self):
        return hash((
            self.name(),
            self.url(),
            self.license_type(),
        ))


class Vulnerability(ModelBase):
    def historical(self):
        return not self.raw.get('exact')

    def cve(self):
        return self.raw.get('vuln').get('cve')

    def cve_severity_str(self, cvss_version):
        if cvss_version is CVSSVersion.V3:
            return str(self.raw.get('vuln').get('cvss3_score'))
        elif cvss_version is CVSSVersion.V2:
            return str(self.raw.get('vuln').get('cvss'))
        else:
            raise NotImplementedError(f'{cvss_version} not supported')

    def has_triage(self) -> bool:
        return bool(self.raw.get('triage')) or bool(self.raw.get('triages'))

    def triages(self) -> 'Iterable[Triage]':
        if not self.has_triage():
            return ()
        trs = self.raw.get('triage')
        if not trs:
            trs = self.raw.get('triages')

        return (Triage(raw_dict=raw) for raw in trs)

    def cve_major_severity(self, cvss_version) -> int:
        if self.cve_severity_str(cvss_version):
            return int(self.cve_severity_str(cvss_version).split('.')[0])
        else:
            return -1

    def __repr__(self):
        return f'{self.__class__.__name__}: {self.cve()}'


class TriageScope(Enum):
    ACCOUNT_WIDE = 'CA'
    FILE_NAME = 'FN'
    FILE_HASH = 'FH'
    RESULT = 'R'
    GROUP = 'G'


class Triage(ModelBase):
    def id(self):
        return self.raw['id']

    def vulnerability_id(self):
        return self.raw['vuln_id']

    def component_name(self):
        return self.raw['component']

    def component_version(self):
        return self.raw['version']

    def scope(self) -> TriageScope:
        return TriageScope(self.raw['scope'])

    def reason(self):
        return self.raw['reason']

    def description(self):
        return self.raw.get('description')

    def __repr__(self):
        return (
            f'{self.__class__.__name__}: {self.id()} '
            f'({self.component_name()} {self.component_version()}, {self.vulnerability_id()})'
        )

    def __eq__(self, other):
        if not isinstance(other, Triage):
            return False
        if self.vulnerability_id() != other.vulnerability_id():
            return False
        if self.component_name() != other.component_name():
            return False
        if self.description() != other.description():
            return False
        return True

    def __hash__(self):
        return hash((self.vulnerability_id(), self.component_name(), self.description()))


# --- wrappers for inofficial protecode API responses


class ScanResult(ModelBase):
    def name(self):
        return self.raw.get('filename', '<None>')

    def is_stale(self) -> bool:
        '''
        Returns a boolean value indicating whether or not the stored scan result
        has become "stale" (meaning that a rescan would potentially return different
        results).
        '''
        return self.raw.get('stale')

    def has_binary(self) -> bool:
        '''
        Returns a boolean value indicating whether or not the uploaded file is still present.
        In case the uploaded file is no longer present, it needs to be re-uploaded prior to
        rescanning.
        '''
        return self.raw.get('rescan-possible')


def highest_major_cve_severity(
    vulnerabilites: Iterable[Vulnerability],
    cvss_version,
) -> int:
    try:
        return max(
            map(lambda v: v.cve_major_severity(cvss_version), vulnerabilites)
        )
    except ValueError:
        return -1


#############################################################################
## upload result model

class UploadStatus(Enum):
    SKIPPED = 1
    PENDING = 2
    DONE = 4


class UploadResult:
    def __init__(
            self,
            status: UploadStatus,
            component: Component,
            result: AnalysisResult,
            pdf_report_retrieval_func,
            resource=None,
    ):
        self.status = ci.util.not_none(status)
        self.component = ci.util.not_none(component)
        if result:
            self.result = result
        else:
            self.result = None
        self.resource = resource
        self._pdf_report_retrieval_func = pdf_report_retrieval_func

    def __str__(self):
        return '{c} - {s}'.format(
            c=self.component.name,
            s=self.status
        )

    def pdf_report(self):
        return self._pdf_report_retrieval_func()

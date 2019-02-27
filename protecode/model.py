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

from enum import Enum
from typing import Iterable

from model.base import ModelBase


class ProcessingStatus(Enum):
    BUSY = 'B'
    READY = 'R'
    FAILED = 'F'


class AnalysisResult(ModelBase):
    def product_id(self):
        return self.raw.get('product_id')

    def display_name(self):
        return self.raw.get('filename', '<None>')

    def status(self) -> ProcessingStatus:
        return ProcessingStatus(self.raw.get('status'))

    def components(self) -> 'Iterable[Component]':
        return (Component(raw_dict=raw) for raw in self.raw.get('components'))

    def custom_data(self):
        return self.raw.get('custom_data')


class Component(ModelBase):
    def name(self):
        return self.raw.get('lib')

    def vulnerabilities(self) -> 'Iterable[Vulnerability]':
        return (Vulnerability(raw_dict=raw) for raw in self.raw.get('vulns'))

    def license(self) -> 'License':
        license_raw = self.raw.get('license', None)
        if not license_raw:
            return None
        return License(raw_dict=license_raw)


class License(ModelBase):
    def name(self):
        return self.raw.get('name')

    def license_type(self):
        return self.raw.get('type')

    def url(self):
        return self.raw.get('url')


class Vulnerability(ModelBase):
    def historical(self):
        return not self.raw.get('exact')

    def cve(self):
        return self.raw.get('vuln').get('cve')

    def cve_severity_str(self):
        return str(self.raw.get('vuln').get('cvss'))

    def has_triage(self):
        return self.raw.get('triage') is not None

    def triages(self) -> 'Iterable[Triage]':
        if not self.has_triage():
            return ()
        return (Triage(raw_dict=raw) for raw in self.raw.get('triage'))

    def cve_major_severity(self) -> int:
        if self.cve_severity_str():
            return int(self.cve_severity_str().split('.')[0])
        else:
            return -1


class TriageScope(Enum):
    ACCOUNT_WIDE = 'CA'
    FILE_NAME = 'FN'
    FILE_HASH = 'FH'
    RESULT = 'R'
    GROUP = 'G'


class Triage(ModelBase):
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


# --- wrappers for inofficial protecode API responses


class ScanResult(ModelBase):
    def name(self):
        return self.raw.get('name')

    def is_stale(self) -> bool:
        '''
        Returns a boolean value indicating whether or not the stored scan result
        has become "stale" (meaning that a rescan would potentially return different
        results).
        '''
        return self.raw.get('is_stale')

    def has_binary(self) -> bool:
        '''
        Returns a boolean value indicating whether or not the uploaded file is still present.
        In case the uploaded file is no longer present, it needs to be re-uploaded prior to
        rescanning.
        '''
        return self.raw.get('has_binary')


def highest_major_cve_severity(vulnerabilites: Iterable[Vulnerability]) -> int:
    try:
        return max(
            map(lambda v: v.cve_major_severity(), vulnerabilites)
        )
    except ValueError:
        return -1

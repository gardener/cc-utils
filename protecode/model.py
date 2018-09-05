# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

    def highest_major_cve_severity(self) -> int:
        try:
            return max(
                map(
                    lambda v: v.cve_major_severity(),
                    filter(lambda v: not v.historical(), self.vulnerabilities())
                )
            )
        except ValueError:
            return -1


class Vulnerability(ModelBase):
    def historical(self):
        return not self.raw.get('exact')

    def cve(self):
        return self.raw.get('vuln').get('cve')

    def cve_severity_str(self):
        return str(self.raw.get('vuln').get('cvss'))

    def cve_major_severity(self) -> int:
        if self.cve_severity_str():
            return int(self.cve_severity_str().split('.')[0])
        else:
            return -1


# --- wrappers for inofficial protecode API responses


class ScanResult(ModelBase):
    def name(self):
        return self.raw.get('name')

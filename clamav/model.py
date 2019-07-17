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

import enum
import json
import sseclient
import typing

from model import ModelBase


class ClamAVInfo(ModelBase):
    def max_scan_size_octets(self) -> int:
        return self.raw['maxScanSize']

    def signature_timestamp(self) -> str:
        return self.raw['signatureTimestamp']

    def engine_version(self) -> str:
        return self.raw['engineVersion']


class ClamAVMemoryInfo(ModelBase):
    def resident_set_size_octets(self) -> int:
        return self.raw['rss']

    def heap_total_octets(self) -> int:
        return self.raw['heapTotal']

    def heap_used_octets(self) -> int:
        return self.raw['heapUsed']

    def external_octets(self) -> int:
        return self.raw['external']


class ClamAVAggregateLoad(ModelBase):
    def load(self) -> float:
        return self.raw['load']

    def scanned_megabytes(self) -> float:
        return self.raw['scanned_MB']


class ClamAVRequestLoad(ModelBase):
    def current(self) -> int:
        return self.raw['current']

    def last_10_min(self) -> ClamAVAggregateLoad:
        return ClamAVAggregateLoad(self.raw['last_10_min'])

    def last_5_min(self) -> ClamAVAggregateLoad:
        return ClamAVAggregateLoad(self.raw['last_5_min'])

    def last_3_min(self) -> ClamAVAggregateLoad:
        return ClamAVAggregateLoad(self.raw['last_3_min'])

    def last_1_min(self) -> ClamAVAggregateLoad:
        return ClamAVAggregateLoad(self.raw['last_1_min'])


class ClamAVMonitoringInfo(ModelBase):
    def memory(self) -> ClamAVMemoryInfo:
        return ClamAVMemoryInfo(self.raw['memory'])

    def uptime(self) -> float:
        return self.raw['uptime']

    def request_load(self) -> ClamAVRequestLoad:
        return ClamAVRequestLoad(self.raw['requestLoad'])

    def signature_age(self) -> int:
        '''Return time since last signature update (in hours)
        '''
        return self.raw['signatureAge']

    def kernel_info(self) -> str:
        return self.raw['kernelInfo']


class ClamAVScanResult(ModelBase):
    def malware_detected(self) -> bool:
        return self.raw['malwareDetected']

    def encrypted_content_detected(self) -> bool:
        return self.raw['encryptedContentDetected']

    def scan_size_octets(self) -> int:
        return self.raw['scanSize']

    def virus_signature(self) -> typing.Union[str, None]:
        '''Return a string describing ClamAV's findings, if any (e.g.: "Eicar-Test-Signature")
        '''
        return self.raw.get('finding')

    def mime_type(self) -> str:
        return self.raw['mimeType']

    def sha_256(self) -> str:
        return self.raw['SHA256']


class ClamAVHealthState(enum.Enum):
    OK = 'OK'
    WARNING = 'WARNING'


class ClamAVHealth(ModelBase):
    def age_hours(self) -> int:
        '''Return hours since last signature update
        '''
        return self.raw['age']

    def state(self) -> ClamAVHealthState:
        return ClamAVHealthState(self.raw['state'])


class ClamAVScanEventTypes(enum.Enum):
    ERROR = 'error'
    RESULT = 'result'


class ClamAVError(ModelBase):
    def status_code(self) -> int:
        '''Return the HTTP/1.1 status code that was given by ClamAV
        '''
        return self.raw['code']

    def message(self) -> str:
        return self.raw['message']


class ClamAVScanEventClient(object):
    '''Client to handle SSE events sent by our k8s ClamAV installation

    Due to the quick timeout in our Infrastructure and the limited functionality our k8s ClamAV
    service provides at this point, we use SSE to keep the connection open.
    For more details, see the "process_events" method
    '''
    def __init__(self, response):
        self.client = sseclient.SSEClient(response)

    def process_events(self) -> typing.Union[ClamAVScanResult, ClamAVError]:
        '''Process the events sent by our ClamAV service

        Our ClamAV service will send exactly one SSE-event which is of one of two types:
            1. An event of type 'error', with its data containing an error-code (a HTTP/1.1 Status
                Code) and a message, in case an error was encountered when scanning.
            2. An event of type 'result' containing the scan result in its data.

        This method blocks until an event is received and then returns an instance of the
        corresponding model class.
        '''
        for event in self.client.events():
            event_type = ClamAVScanEventTypes(event.event)
            if event_type is ClamAVScanEventTypes.ERROR:
                return ClamAVError(json.loads(event.data))
            if event_type is ClamAVScanEventTypes.RESULT:
                return ClamAVScanResult(json.loads(event.data))

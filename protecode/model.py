# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import collections.abc
import dataclasses
import datetime
import enum
import logging
import traceback

import dacite
import dateutil.parser

import ci.util
import dso.cvss
import dso.labels
import gci.componentmodel as cm

from concourse.model.base import (
    AttribSpecMixin,
    AttributeSpec,
)
from model.base import ModelBase


logger = logging.getLogger()


class VersionOverrideScope(enum.Enum):
    APP = 1
    GROUP = 2
    GLOBAL = 3


class ProcessingStatus(enum.Enum):
    BUSY = 'B'
    READY = 'R'
    FAILED = 'F'


class CVSSVersion(enum.Enum):
    V2 = 'CVSSv2'
    V3 = 'CVSSv3'


class Product(ModelBase):
    def product_id(self) -> int:
        return self.raw['product_id']

    def custom_data(self) -> dict[str, str]:
        return self.raw.get('custom_data', dict())

    def name(self) -> str:
        return self.raw['name']


class AnalysisResult(ModelBase):
    def product_id(self) -> int:
        return self.raw.get('product_id')

    def group_id(self) -> int:
        return int(self.raw.get('group_id'))

    def base_url(self) -> str:
        report_url = self.report_url()
        parsed_url = ci.util.urlparse(report_url)
        return f'{parsed_url.scheme}://{parsed_url.hostname}'

    def report_url(self) -> str:
        return self.raw.get('report_url')

    def display_name(self) -> str:
        return self.raw.get('filename', '<None>')

    def name(self):
        return self.raw.get('name')

    def status(self) -> ProcessingStatus:
        return ProcessingStatus(self.raw.get('status'))

    def components(self) -> 'collections.abc.Generator[Component, None, None]':
        return (Component(raw_dict=raw) for raw in self.raw.get('components', []))

    def custom_data(self) -> dict[str, str]:
        return self.raw.get('custom_data')

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

    def creation_time(self) -> str:
        return self.raw.get('created')

    def scanned_bytes(self) -> int:
        return self.raw.get('scanned_bytes')

    def __repr__(self):
        return f'{self.__class__.__name__}: {self.display_name()}({self.product_id()})'


@dataclasses.dataclass
class License:
    name: str
    type: str | None = None
    url: str | None = None


class Component(ModelBase):
    def name(self) -> str:
        return self.raw.get('lib')

    def version(self) -> str:
        return self.raw.get('version')

    def vulnerabilities(self) -> 'collections.abc.Generator[Vulnerability, None, None]':
        for raw in self.raw.get('vulns'):
            if raw['vuln']['cve']:
                yield Vulnerability(raw_dict=raw)
                continue

    @property
    def licenses(self) -> collections.abc.Generator[License, None, None]:
        if not (licenses := self.raw.get('licenses')):
            license_raw = self.raw.get('license')
            if not license_raw:
                return
            yield dacite.from_dict(
                data_class=License,
                data=license_raw,
            )
            return

        yield from (
            dacite.from_dict(
                data_class=License,
                data=license_raw,
            ) for license_raw in licenses.get('licenses')
        )

    def extended_objects(self) -> 'collections.abc.Generator[ExtendedObject, None, None]':
        return (ExtendedObject(raw_dict=raw) for raw in self.raw.get('extended-objects'))

    @property
    def tags(self) -> tuple[str]:
        if not (tags := self.raw.get('tags')):
            return ()
        return tuple(tags)

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


class Vulnerability(ModelBase):
    def historical(self):
        return not self.raw.get('exact')

    def cve(self) -> str:
        return self.raw.get('vuln').get('cve')

    def cve_severity(self, cvss_version=CVSSVersion.V3) -> float:
        if cvss_version is CVSSVersion.V3:
            return float(self.raw.get('vuln').get('cvss3_score'))
        elif cvss_version is CVSSVersion.V2:
            return float(self.raw.get('vuln').get('cvss'))
        else:
            raise NotImplementedError(f'{cvss_version} not supported')

    @property
    def cvss(self) -> dso.cvss.CVSSV3 | None:
        cvss_vector = self.raw['vuln']['cvss3_vector']
        # ignore cvss2_vector for now

        if not cvss_vector:
            return None

        return dso.cvss.CVSSV3.parse(cvss_vector)

    def summary(self) -> str:
        return self.raw.get('vuln').get('summary')

    def has_triage(self) -> bool:
        return bool(self.raw.get('triage')) or bool(self.raw.get('triages'))

    def triages(self) -> 'collections.abc.Generator[Triage, None, None]':
        if not self.has_triage():
            return ()
        trs = self.raw.get('triage')
        if not trs:
            trs = self.raw.get('triages')

        return (Triage(raw_dict=raw) for raw in trs)

    @property
    def published(self) -> datetime.date:
        if not (published := self.raw['vuln'].get('published')):
            return None
        return dateutil.parser.isoparse(published)

    def __repr__(self):
        return f'{self.__class__.__name__}: {self.cve()}'


class TriageScope(enum.Enum):
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

    @property
    def modified(self) -> datetime.datetime:
        return dateutil.parser.isoparse(self.raw.get('modified'))

    def applies_to_same_vulnerability_as(self, other) -> bool:
        if not isinstance(other, Triage):
            return False
        return self.vulnerability_id() == other.vulnerability_id()

    def __repr__(self):
        return (
            f'{self.__class__.__name__}: {self.id()} '
            f'({self.component_name()} {self.component_version()}, '
            f'{self.vulnerability_id()}, Scope: {self.scope().value})'
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


#############################################################################
## upload result model

class UploadStatus(enum.Enum):
    SKIPPED = 1
    PENDING = 2
    DONE = 4


@dataclasses.dataclass
class ScanRequest:
    '''
    a scan request of an artefact (referenced by component and artefact).

    if a previous scan result was found, its "product-id" is stored as `target_product_id`
    '''
    component: cm.Component
    artefact: cm.Artifact
    # The actual content to be scanned.
    scan_content: collections.abc.Generator[bytes, None, None]
    display_name: str
    target_product_id: int | None
    custom_metadata: dict

    def auto_triage_scan(self) -> bool:
        # hardcode auto-triage to be determined by artefact
        artefact = self.artefact

        # pylint: disable=E1101
        if not (label := artefact.find_label(name=dso.labels.BinaryIdScanLabel.name)):
            label = artefact.find_label(name=dso.labels.BinaryIdScanLabel._alt_name)
            if label:
                return True
        if not label:
            return False

        label: dso.labels.BinaryIdScanLabel = dso.labels.deserialise_label(label=label)

        return label.value.policy is dso.labels.ScanPolicy.SKIP

    def __str__(self):
        return (
            f"ScanRequest(name='{self.display_name}', target_product_id='{self.target_product_id}' "
            f"custom_metadata='{self.custom_metadata}')"
        )


class BdbaScanError(Exception):
    def __init__(
        self,
        scan_request: ScanRequest,
        component: cm.Component,
        artefact: cm.Artifact,
        exception=None,
        *args,
        **kwargs,
    ):
        self.scan_request = scan_request
        self.component = component
        self.artefact = artefact
        self.exception = exception

        super().__init__(*args, **kwargs)

    def print_stacktrace(self):
        c = self.component
        a = self.artefact
        name = f'{c.name}:{c.version}/{a.name}:{a.version}'

        if not self.exception:
            return name + ' - no exception available'

        return name + '\n' + ''.join(traceback.format_tb(self.exception.__traceback__))


class ProcessingMode(AttribSpecMixin, enum.Enum):
    RESCAN = 'rescan'
    FORCE_UPLOAD = 'force_upload'

    @classmethod
    def _attribute_specs(cls):
        return (
            AttributeSpec.optional(
                name=cls.RESCAN.value,
                default=None,
                doc='''
                    (re-)scan container images if Protecode indicates this might bear new results.
                    Upload absent images.
                ''',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.FORCE_UPLOAD.value,
                default=None,
                doc='''
                    `always` upload and scan all images.
                ''',
                type=str,
            ),
        )

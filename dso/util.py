import dataclasses
import datetime
import functools
import uuid
import logging

import ci.util
import dso.model
import protecode.model


@functools.lru_cache
def component_logger(name: str):
    return logging.getLogger(name)


def upload_result_to_compliance_issue(
    upload_result: protecode.model.UploadResult,
    datasource: str = dso.model.Datasource.PROTECODE,
) -> dso.model.ComplianceIssue:

    artifact = dataclasses.asdict(
        upload_result.resource,
        dict_factory=ci.util.dict_factory_enum_serialisiation,
    )

    artifact_ref = dso.model.ArtifactReference(
        componentName=upload_result.component.name,
        componentVersion=upload_result.component.version,
        artifact=artifact,
    )

    meta = dso.model.ComplianceIssueMetadata(
        datasource=datasource,
        creationDate=datetime.datetime.now().isoformat(),
        uuid=str(uuid.uuid4()),
    )

    data = [
        {
            'component': f'{c.name()}:{c.version()}',
            'license': c.license().name() if c.license() else 'UNKNOWN',
            'vulnerabilities': [v.cve() for v in c.vulnerabilities()],
        }
        for c in upload_result.result.components()
    ]

    issue = dso.model.ComplianceIssue(
        artifact=artifact_ref,
        meta=meta,
        data={'data': data},
    )
    return issue

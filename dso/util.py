import dataclasses
import datetime
import functools
import uuid
import logging

import dso.deliverydb
import dso.deliverydb.db
import dso.deliverydb.model
import dso.model
import protecode.model


@functools.lru_cache
def component_logger(name: str):
    return logging.getLogger(name)


def upload_result_to_compliance_issue(
    upload_result: protecode.model.UploadResult,
    datasource: dso.model.Datasource = dso.model.Datasource.PROTECODE,
) -> dso.model.ComplianceIssue:

    artifact = dataclasses.asdict(upload_result.resource)
    artifact['type'] = upload_result.resource.type.name
    artifact['access']['type'] = upload_result.resource.access.type.name
    artifact['relation'] = upload_result.resource.relation.name

    id = dso.model.ComplianceIssueId(
        componentName=upload_result.component.name,
        componentVersion=upload_result.component.version,
        artifact=artifact,
    )

    meta = dso.model.ComplianceIssueMeta(
        datasource=datasource.name,
        creationDate=datetime.datetime.now().isoformat(),
        uuid=str(uuid.uuid4()),
    )

    data = [
        {
            'component': f'{c.name()}:{c.version()}',
            'license': c.license().name(),
            'vulnerabilities': [v.cve() for v in c.vulnerabilities()],
        }
        for c in upload_result.result.components()
    ]

    issue = dso.model.ComplianceIssue(
        id=id,
        meta=meta,
        data={'data': data},
    )
    return issue

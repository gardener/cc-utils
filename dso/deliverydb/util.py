import dataclasses
import datetime
import logging
import typing
import uuid

import dso.deliverydb
import dso.deliverydb.db
import dso.deliverydb.model
import dso.model
import protecode.model


logger = logging.getLogger(__name__)


def insert_compliance_issue(
    db: dso.deliverydb.db.DeliveryDB,
    datasource: dso.model.Datasource,
    # extend typehints with more integrations
    scan: typing.Union[protecode.model.UploadResult],
):
    def _insert(
        issue: dso.model.ComplianceIssue,
        db: dso.deliverydb.db.DeliveryDB,
    ):
        logger.info('inserting result to deliverydb')
        scan = dso.deliverydb.model.Scan(
            id=dataclasses.asdict(issue.id),
            meta=dataclasses.asdict(issue.meta),
            data=issue.data,
        )

        db.Session.add(scan)
        db.Session.commit()

    def _parse_protecode(
        scan: protecode.model.UploadResult,
    ):
        artifact = dataclasses.asdict(scan.resource)
        artifact['type'] = scan.resource.type.name
        artifact['access']['type'] = scan.resource.access.type.name
        artifact['relation'] = scan.resource.relation.name

        id = dso.model.ComplianceIssueId(
            componentName=scan.component.name,
            componentVersion=scan.component.version,
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
            for c in scan.result.components()
        ]

        issue = dso.model.ComplianceIssue(
            id=id,
            meta=meta,
            data={'data': data},
        )
        return issue

    if datasource is dso.model.Datasource.PROTECODE:
        issue = _parse_protecode(
            scan=scan,
        )
        _insert(
            issue=issue,
            db=db,
        )
    else:
        raise NotImplementedError(datasource)

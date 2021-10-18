import dataclasses
import datetime
import logging
import uuid

import dso.deliverydb
import dso.deliverydb.db
import dso.deliverydb.model
import dso.model
import protecode.model


logger = logging.getLogger(__name__)


def insert_compliance_issue(
    deliverydb_cfg_name: str,
    datasource: dso.model.Datasource,
    scan,
):
    def _insert(
        issue: dso.model.ComplianceIssue,
        deliverydb_cfg_name: str,
    ):
        logger.info('inserting result to deliverydb')
        db = dso.deliverydb.db.make_deliverydb(
            deliverydb_cfg_name=deliverydb_cfg_name,
        )

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
            deliverydb_cfg_name=deliverydb_cfg_name,
        )
    elif datasource in \
    [dso.model.Datasource.WHITESOURCE, dso.model.Datasource.CHECKMARX, dso.model.Datasource.CLAMAV]:
        raise NotImplementedError

    else:
        raise Exception(f'{datasource=} not supported')

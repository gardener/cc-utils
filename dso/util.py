import functools
import logging
import uuid

import dso.model
import dso.compliancedb.db
import dso.compliancedb.model


logger = logging.getLogger(__name__)


@functools.lru_cache
def component_logger(name: str):
    return logging.getLogger(name)


def scans_to_compliance_db(
    scan_data,
    tool: dso.compliancedb.model.ScanTool,
    component_name: str,
    component_version: str,
    artifact_name: str,
    artifact_version: str,
    artifact_type: dso.model.ArtifactType,
    compliancedb_cfg_name: str,
):
    if not compliancedb_cfg_name:
        logger.warning('no compliance db cfg name found, skipping insertion')
        return
    logger.info('inserting results to database')

    db_fac = dso.compliancedb.db.ComplianceDBFactory(cfg_name=compliancedb_cfg_name)
    cdb = db_fac.make()

    # TODO ensure uuids are actually unique
    artifact_id = uuid.uuid4()
    artifact = dso.compliancedb.model.Artifact(
        id=artifact_id,
        component_name=component_name,
        component_version=component_version,
        name=artifact_name,
        version=artifact_version,
        type=artifact_type
    )

    scan_result = dso.compliancedb.model.ScanResult(
        id=uuid.uuid4(),
        artifact_id=artifact_id,
        source=tool,
        scan_data=scan_data,
    )
    cdb.Session.add(artifact)
    cdb.Session.add(scan_result)
    cdb.Session.commit()

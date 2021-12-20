import logging

import ci.log
import concourse.replicator
import model.concourse

ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


## it may seem pointless to wrap replicate-pipelines - however, this will at least help
## linters to detect errors that would be missed if the call were inlined in mako
def replicate_pipelines(
    cfg_set,
    job_mapping: model.concourse.JobMapping,
    pipelines_not_to_delete: list,
):
    # prevent own replication pipeline from being removed
    def filter_own_pipeline(pipeline_name: str):
        return pipeline_name in pipelines_not_to_delete

    result = concourse.replicator.replicate_pipelines(
        cfg_set=cfg_set,
        job_mapping=job_mapping,
        unpause_pipelines=job_mapping.unpause_deployed_pipelines(),
        unpause_new_pipelines=job_mapping.unpause_new_pipelines(),
        expose_pipelines=job_mapping.expose_deployed_pipelines(),
        remove_pipelines_filter=filter_own_pipeline,
    )
    return result

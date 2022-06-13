
import logging
import traceback

import concourse.client.api
import concourse.client.model
import concourse.enumerator
import concourse.replicator


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def trigger_resource_check(
    concourse_api: concourse.client.api.ConcourseApiBase,
    resources,
):
    logger.debug('trigger_resource_check')
    for resource in resources:
        logger.info('triggering resource check for: ' + resource.name)
        try:
            concourse_api.trigger_resource_check(
                pipeline_name=resource.pipeline_name(),
                resource_name=resource.name,
            )
        except Exception:
            traceback.print_exc()

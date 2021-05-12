import logging

import ci.log

ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def replicate_secrets():
    logger.info('Here would we render the pipeline. If we had one...')

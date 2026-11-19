import logging
import time

import github3.exceptions

logger = logging.getLogger(__name__)


def retry_and_throttle(function: callable, retries=5):
    '''
    decorator intended to be used for retrying/throttling functions issueing github-api-requests
    that will sporadically run into quota-issues. There is a hard-coded sleep of 60s between
    retries. Any other errors than github3.exceptions.ForbiddenError are ignored. After configured
    amount of retries, last exception is re-raised.
    '''
    def call_with_retry(*args, retries=retries, **kwargs):
        try:
            return function(*args, **kwargs)
        except github3.exceptions.ForbiddenError as fbe:
            if retries <= 0:
                raise

            if isinstance(fbe.message, bytes):
                message = fbe.message.decode('utf-8')
            else:
                message = fbe.message
            if not 'exceeded' in message:
                raise

            retries -= 1

            logger.warning(f'error from github: {fbe.message=} {retries=}')
            time.sleep(60 * 1) # 1m
            return call_with_retry(*args, retries=retries, **kwargs)

    return call_with_retry

import logging
import time

import cachetools
import github3.github

logger = logging.getLogger(__name__)

_user_cache = cachetools.TTLCache(maxsize=512, ttl=60*60*12) # 12h


def is_user_active(
    username: str,
    github: github3.github.GitHub,
    retries: int=3,
    cache: cachetools.Cache=_user_cache,
):
    have_cache = isinstance(cache, cachetools.Cache)

    if have_cache and (github, username) in cache:
        return cache[(github, username)]

    def store_user_status(status: bool):
        if not have_cache:
            print('no')
            return status
        cache[(github, username)] = status
        return status

    try:
        user = github.user(username)
        if user.as_dict().get('suspended_at'):
            return store_user_status(False)
        return store_user_status(True)
    except github3.exceptions.NotFoundError:
        logger.warning(f'{username=} not found')
        return store_user_status(False)
    except github3.exceptions.ForbiddenError as fbe:
        logger.warning(f'{fbe.errors=} {fbe.message=}')

        if not retries:
            raise

        logger.warning(f'received http-403 - maybe due to quota - will retry {retries=}')
        time.sleep(60)
        return is_user_active(
            username=username,
            github=github,
            retries=retries - 1
        )

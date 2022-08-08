import logging

import delivery.model
import unixutil.model as um


logger = logging.getLogger(__name__)


def find_branch_info(
    os_id: um.OperatingSystemId,
    os_infos: list[delivery.model.OsReleaseInfo],
) -> delivery.model.OsReleaseInfo:
    if not os_id.ID:
        return None # os-id could not be determined

    os_version = os_id.VERSION_ID

    def version_candidates():
        yield os_version
        yield f'v{os_version}'

        parts = os_version.split('.')

        if len(parts) == 1:
            return

        yield parts[0]
        yield 'v' + parts[0]

        yield '.'.join(parts[:2]) # strip parts after minor
        yield 'v' + '.'.join(parts[:2]) # strip parts after minor

    candidates = tuple(version_candidates())

    for os_info in os_infos:
        for candidate in candidates:
            if os_info.name == candidate:
                return os_info

    logger.warning(f'did not find branch-info for {os_id=}')

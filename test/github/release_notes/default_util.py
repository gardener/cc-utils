# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from github.release_notes.util import (
    ReleaseNote,
    extract_release_notes,
)
from github.release_notes.model import (
    ReleaseNoteBlock,
    ReferenceType,
    REF_TYPE_PULL_REQUEST,
)
from github.release_notes.renderer import (
    CATEGORY_IMPROVEMENT_ID,
    TARGET_GROUP_USER_ID,
)
from product.model import ComponentName

CURRENT_REPO_NAME = 'github.com/madeup/current-repo'
CURRENT_REPO = ComponentName(CURRENT_REPO_NAME)

DEFAULT_CATEGORY = CATEGORY_IMPROVEMENT_ID
DEFAULT_TARGET_GROUP = TARGET_GROUP_USER_ID
DEFAULT_RELEASE_NOTE_TEXT = 'default release note text'
DEFAULT_USER = 'foo'
DEFAULT_REFERENCE_ID = '42'
DEFAULT_REFERENCE_TYPE = REF_TYPE_PULL_REQUEST
DEFAULT_REPO = CURRENT_REPO


def release_note_block_with_defaults(
    category_id: str=DEFAULT_CATEGORY,
    target_group_id: str=DEFAULT_TARGET_GROUP,
    text: str=DEFAULT_RELEASE_NOTE_TEXT,
    reference_type: ReferenceType=DEFAULT_REFERENCE_TYPE,
    reference_id: str=DEFAULT_REFERENCE_ID,
    user_login: str=DEFAULT_USER,
    source_repo: str=CURRENT_REPO_NAME,
    cn_current_repo: ComponentName=DEFAULT_REPO,
) -> ReleaseNoteBlock:
    """
    unit tests can expect the default values to be stable
    """
    return ReleaseNoteBlock(
        category_id=category_id,
        target_group_id=target_group_id,
        text=text,
        reference_type=reference_type,
        reference_id=reference_id,
        user_login=user_login,
        source_repo=source_repo,
        cn_current_repo=cn_current_repo
    )


def extract_release_notes_with_defaults(
    reference_id: str=DEFAULT_REFERENCE_ID,
    reference_type: ReferenceType=DEFAULT_REFERENCE_TYPE,
    text: str=DEFAULT_RELEASE_NOTE_TEXT,
    user_login: str=DEFAULT_USER,
    cn_current_repo: ComponentName=DEFAULT_REPO,
) -> [ReleaseNote]:
    return extract_release_notes(
            reference_id=reference_id,
            reference_type=reference_type,
            text=text,
            user_login=user_login,
            cn_current_repo=cn_current_repo
        )

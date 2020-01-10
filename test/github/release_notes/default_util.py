# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

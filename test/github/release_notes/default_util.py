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
import gci
import typing

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
from unittest.mock import MagicMock

DEFAULT_CATEGORY = CATEGORY_IMPROVEMENT_ID
DEFAULT_TARGET_GROUP = TARGET_GROUP_USER_ID
DEFAULT_RELEASE_NOTE_TEXT = 'default release note text'
DEFAULT_USER = 'foo'
DEFAULT_REFERENCE_ID = '42'
DEFAULT_REFERENCE_TYPE = REF_TYPE_PULL_REQUEST


def create_mock_component(
    hostname: str,
    org_name: str,
    repo_name: str,
    component_name: str=None,
):
    gh_access_mock = MagicMock(spec=gci.componentmodel.GithubAccess)
    gh_access_mock.repository_name.return_value = repo_name
    gh_access_mock.org_name.return_value = org_name
    gh_access_mock.hostname.return_value = hostname

    gh_access_mock.repoUrl = f'https://{hostname}/{org_name}/{repo_name}'

    mock_source = MagicMock(spec=gci.componentmodel.ComponentSource)
    mock_source.find_label.return_value = None
    mock_source.access = gh_access_mock

    ctx_mock = MagicMock(spec=gci.componentmodel.OciRepositoryContext)
    ctx_mock.baseUrl = f'{hostname}/{org_name}/{repo_name}'

    mock_component = MagicMock(spec=gci.componentmodel.Component)
    mock_component.current_repository_ctx.return_value = ctx_mock
    mock_component.sources = [mock_source]

    if component_name is not None:
        mock_component.name = component_name
    else:
        mock_component.name = f'{hostname}/{org_name}/{repo_name}'

    return mock_component


CURRENT_COMPONENT_HOSTNAME = 'github.com'
CURRENT_COMPONENT_ORG_NAME = 'madeup'
CURRENT_COMPONENT_REPO_NAME = 'current-repo'

CURRENT_COMPONENT = create_mock_component(
    hostname=CURRENT_COMPONENT_HOSTNAME,
    org_name=CURRENT_COMPONENT_ORG_NAME,
    repo_name=CURRENT_COMPONENT_REPO_NAME,
)


def release_note_block_with_defaults(
    category_id: str=DEFAULT_CATEGORY,
    target_group_id: str=DEFAULT_TARGET_GROUP,
    text: str=DEFAULT_RELEASE_NOTE_TEXT,
    reference_type: ReferenceType=DEFAULT_REFERENCE_TYPE,
    reference_id: str=DEFAULT_REFERENCE_ID,
    user_login: str=DEFAULT_USER,
    source_component_hostname: str=CURRENT_COMPONENT_HOSTNAME,
    source_component_org_name: str=CURRENT_COMPONENT_ORG_NAME,
    source_component_repo_name: str=CURRENT_COMPONENT_REPO_NAME,
    current_component_hostname: str=CURRENT_COMPONENT_HOSTNAME,
    current_component_org_name: str=CURRENT_COMPONENT_ORG_NAME,
    current_component_repo_name: str=CURRENT_COMPONENT_REPO_NAME,
) -> ReleaseNoteBlock:
    """
    unit tests can expect the default values to be stable
    """
    if (
        source_component_hostname
        and source_component_org_name
        and source_component_repo_name
    ):
        source_component = create_mock_component(
                hostname=source_component_hostname,
                org_name=source_component_org_name,
                repo_name=source_component_repo_name,
            )
    else:
        source_component = None

    return ReleaseNoteBlock(
        category_id=category_id,
        target_group_id=target_group_id,
        text=text,
        reference_type=reference_type,
        reference_id=reference_id,
        user_login=user_login,
        source_component=source_component,
        current_component=create_mock_component(
            hostname=current_component_hostname,
            org_name=current_component_org_name,
            repo_name=current_component_repo_name,
        ),
    )


def extract_release_notes_with_defaults(
    reference_id: str=DEFAULT_REFERENCE_ID,
    reference_type: ReferenceType=DEFAULT_REFERENCE_TYPE,
    text: str=DEFAULT_RELEASE_NOTE_TEXT,
    user_login: str=DEFAULT_USER,
    current_component_repo_name: str =CURRENT_COMPONENT_REPO_NAME,
    current_component_hostname: str =CURRENT_COMPONENT_HOSTNAME,
    current_component_org_name: str =CURRENT_COMPONENT_ORG_NAME,
    source_component_hostname: str=None,
    source_component_org_name: str=None,
    source_component_repo_name: str=None,
) -> typing.List[ReleaseNote]:

    if (
        source_component_hostname
        and source_component_org_name
        and source_component_repo_name
    ):
        source_component = create_mock_component(
                hostname=source_component_hostname,
                org_name=source_component_org_name,
                repo_name=source_component_repo_name,
            )
    else:
        source_component = None

    return extract_release_notes(
            reference_id=reference_id,
            reference_type=reference_type,
            text=text,
            user_login=user_login,
            current_component=create_mock_component(
                hostname=current_component_hostname,
                org_name=current_component_org_name,
                repo_name=current_component_repo_name,
            ),
            source_component=source_component,
        )

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
from dataclasses import dataclass, field
import gci
from pydash import _

import ci.util
import cnudie.util


@dataclass
class Commit:
    hash: str
    subject: str
    message: str


@dataclass
class ReferenceType:
    identifier: str # reference type identifier in release notes block
    prefix: str # reference prefix that is used for the rendered text
    github_api_resource_type: str # "pull" or "commit"


REF_TYPE_PULL_REQUEST = ReferenceType(
    identifier='#',
    prefix='#',
    github_api_resource_type='pull'
)

REF_TYPE_COMMIT = ReferenceType(
    identifier='$',
    prefix='@',
    github_api_resource_type='commit'
)
REFERENCE_TYPES = [REF_TYPE_PULL_REQUEST, REF_TYPE_COMMIT]


@dataclass
class Reference:
    type: ReferenceType
    identifier: str


def reference_type_for_type_identifier(
    reference_type_identifier: str
):
    return _.find(REFERENCE_TYPES,
        lambda ref_type: ref_type.identifier == reference_type_identifier
    )


@dataclass
class ReleaseNote:
    category_id: str
    target_group_id: str
    text: str
    reference: Reference
    user_login: str
    is_current_repo: bool
    from_same_github_instance: str
    source_component: gci.componentmodel.Component = field(compare=False)


class ReleaseNoteBlock(ReleaseNote):
    def __init__(
        self,
        category_id: str,
        target_group_id: str,
        text: str,
        reference_type: ReferenceType,
        reference_id: str,
        user_login: str,
        current_component: gci.componentmodel.Component,
        source_component: gci.componentmodel.Component,
    ):
        if reference_id:
            ci.util.check_type(reference_id, str)

        ci.util.not_none(source_component)

        reference = Reference(type=reference_type, identifier=reference_id)

        source_component_access = cnudie.util.determine_main_source_for_component(
            source_component
        ).access
        current_component_access = cnudie.util.determine_main_source_for_component(
            current_component
        ).access

        is_current_repo = current_component.name == source_component.name

        current_hostname = current_component_access.hostname()
        source_hostname = source_component_access.hostname()
        from_same_github_instance = current_hostname in source_hostname

        super().__init__(
            category_id,
            target_group_id,
            text,
            reference,
            user_login,
            is_current_repo,
            from_same_github_instance,
            source_component,
        )

    def ref(self):
        if not self.reference.identifier:
            return ''
        return ' {ref_type}{ref_id}'.format(
            ref_type=self.reference.type.identifier,
            ref_id=self.reference.identifier
        )

    def user(self):
        if not self.user_login:
            return ''
        return ' @{user}'.format(user=self.user_login)

    def to_block_str(self):
        return ('``` {cat} {t_grp} {src_repo}{ref}{user}\n'
            '{text}\n'
            '```'.format(
                cat=self.category_id,
                t_grp=self.target_group_id,
                src_repo=self.source_component.name,
                ref=self.ref(),
                user=self.user(),
                text=self.text
            ))

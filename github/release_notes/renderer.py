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

from abc import abstractmethod
from collections import namedtuple
from pydash import _

from github.release_notes.model import ReleaseNote, REF_TYPE_COMMIT


def get_or_call(obj, path):
    value = _.get(obj, path)
    if callable(value):
        return value()
    return value


TitleNode = namedtuple("TitleNode", ["identifier", "title", "nodes", "matches_rls_note_field_path"])
TARGET_GROUP_USER_ID = 'user'
TARGET_GROUP_USER = TitleNode(
    identifier=TARGET_GROUP_USER_ID,
    title='USER',
    nodes=None,
    matches_rls_note_field_path='target_group_id'
)
TARGET_GROUP_OPERATOR_ID = 'operator'
TARGET_GROUP_OPERATOR = TitleNode(
    identifier=TARGET_GROUP_OPERATOR_ID,
    title='OPERATOR',
    nodes=None,
    matches_rls_note_field_path='target_group_id'
)
TARGET_GROUP_DEVELOPER_ID = 'developer'
TARGET_GROUP_DEVELOPER = TitleNode(
    identifier=TARGET_GROUP_DEVELOPER_ID,
    title='DEVELOPER',
    nodes=None,
    matches_rls_note_field_path='target_group_id'
)
TARGET_GROUPS = [TARGET_GROUP_USER, TARGET_GROUP_OPERATOR, TARGET_GROUP_DEVELOPER]

CATEGORY_ACTION_ID = 'action'
CATEGORY_ACTION = TitleNode(
    identifier=CATEGORY_ACTION_ID,
    title='Action Required',
    nodes=TARGET_GROUPS,
    matches_rls_note_field_path='category_id'
)
CATEGORY_NOTEWORTHY_ID = 'noteworthy'
CATEGORY_NOTEWORTHY = TitleNode(
    identifier=CATEGORY_NOTEWORTHY_ID,
    title='Most notable changes',
    nodes=TARGET_GROUPS,
    matches_rls_note_field_path='category_id'
)
CATEGORY_IMPROVEMENT_ID = 'improvement'
CATEGORY_IMPROVEMENT = TitleNode(
    identifier=CATEGORY_IMPROVEMENT_ID,
    title='Improvements',
    nodes=TARGET_GROUPS,
    matches_rls_note_field_path='category_id'
)
CATEGORIES = [CATEGORY_ACTION, CATEGORY_NOTEWORTHY, CATEGORY_IMPROVEMENT]


class Renderer(object):
    def __init__(self, release_note_objs: [ReleaseNote]):
        self.rls_note_objs = _.uniq(release_note_objs)

    def render(self) -> str:
        origin_nodes = _\
            .chain(self.rls_note_objs)\
            .sort_by(lambda rls_note_obj: rls_note_obj.cn_source_repo.github_repo())\
            .sort_by(lambda rls_note_obj: rls_note_obj.is_current_repo, reverse=True)\
            .uniq_by(lambda rls_note_obj: rls_note_obj.cn_source_repo.name())\
            .map(lambda rls_note_obj: TitleNode(
                identifier=rls_note_obj.cn_source_repo.name(),
                title='[{origin_name}]'.format(
                    origin_name=rls_note_obj.cn_source_repo.github_repo()
                ),
                nodes=CATEGORIES,
                matches_rls_note_field_path='cn_source_repo.name' # path points to a function
            ))\
            .value()

        release_note_lines = self._to_release_note_lines(
            nodes=origin_nodes,
            level=1,
            rls_note_objs=self.rls_note_objs
        )

        if not release_note_lines:
            return 'no release notes available'
        return '\n'.join(release_note_lines)

    def _to_release_note_lines(
        self,
        nodes: [TitleNode],
        level: int,
        rls_note_objs: [ReleaseNote]
    ) -> [str]:
        lines = list()
        for node in nodes:
            filtered_rls_note_objects = _.filter(
                rls_note_objs,
                lambda rls_note_obj:
                    node.identifier == get_or_call(rls_note_obj, node.matches_rls_note_field_path)
            )
            if not filtered_rls_note_objects:
                continue
            if node.nodes:
                release_note_lines = self._to_release_note_lines(
                    nodes=node.nodes,
                    level=level + 1,
                    rls_note_objs=filtered_rls_note_objects
                )
                lines.append(self._title(node, level))
                lines.extend(release_note_lines)
            else:
                bullet_points = self._to_bullet_points(
                    tag=node.title,
                    rls_note_objs=filtered_rls_note_objects
                )
                # title is used as bullet point tag -> no need for additional title
                lines.extend(bullet_points)

        return lines

    def _header_suffix(
        self,
        rls_note_obj: ReleaseNote
    ) -> str:
        if not rls_note_obj.user_login and not rls_note_obj.reference.identifier:
            return ''

        header_suffix_list = list()
        if rls_note_obj.reference.identifier:
            header_suffix_list.append(self._header_suffix_reference(rls_note_obj))
        if rls_note_obj.user_login:
            header_suffix_list.append(self._header_suffix_user(rls_note_obj))

        header_suffix = ' ({s})'.format(
            s=', '.join(header_suffix_list)
        )
        return header_suffix

    def _header_suffix_reference(
        self,
        rls_note_obj: ReleaseNote
    ):
        reference_id_text = rls_note_obj.reference.identifier
        reference_prefix = rls_note_obj.reference.type.prefix

        should_generate_link = self._generate_link(rls_note_obj)

        is_reference_auto_linked = rls_note_obj.is_current_repo and not should_generate_link
        if rls_note_obj.reference.type == REF_TYPE_COMMIT:
            if is_reference_auto_linked:
                # for the current repo we use gitHub's feature to auto-link to references,
                # hence in case of commits we don't need a prefix
                reference_prefix = ''
            if should_generate_link:
                reference_id_text = rls_note_obj.reference.identifier[0:12] # short commit hash

        reference = '{reference_prefix}{ref_id}'.format(
            reference_prefix=reference_prefix,
            ref_id=reference_id_text,
        )

        if is_reference_auto_linked:
            return reference

        if not should_generate_link:
            # returns e.g. gardener/cc-utils#42 or g. gardener/cc-utils@commit-hash
            return '{repo_path}{reference}'.format(
                    repo_path=rls_note_obj.cn_source_repo.github_repo_path(),
                    reference=reference
                )

        return self._github_reference_link(
            rls_note_obj=rls_note_obj,
            reference=reference
        )

    def _header_suffix_user(
        self,
        rls_note_obj: ReleaseNote
    ):
        is_user_auto_linked = not self._generate_link(rls_note_obj)
        if is_user_auto_linked:
            return '@{u}'.format(
                u=rls_note_obj.user_login
            )

        return self._github_user_profile_link(
            user=rls_note_obj.user_login,
            github_url=rls_note_obj.cn_source_repo.github_url()
        )

    def _github_reference_link(
        self,
        rls_note_obj: ReleaseNote,
        reference: str
    ) -> str:
        reference_link = '{source_repo_url}/{github_api_resource_type}/{ref_id}'.format(
            source_repo_url=rls_note_obj.cn_source_repo.github_repo_url(),
            ref_id=rls_note_obj.reference.identifier,
            github_api_resource_type=rls_note_obj.reference.type.github_api_resource_type
        )

        link_text = '{repo_path}{reference}'.format(
            repo_path=rls_note_obj.cn_source_repo.github_repo_path(),
            reference=reference
        )
        return self._build_link(url=reference_link, text=link_text)

    def _github_user_profile_link(
        self,
        user: str,
        github_url: str
    ) -> str:
        user_link_text = '@{u}'.format(u=user)
        user_url = '{github_url}/{u}'.format(
            u=user,
            github_url=github_url
        )
        return self._build_link(url=user_url, text=user_link_text)

    def _to_bullet_points(
        self,
        tag: str,
        rls_note_objs: [ReleaseNote],
    ):
        bullet_points = list()
        for rls_note_obj in rls_note_objs:
            for i, rls_note_line in enumerate(rls_note_obj.text.splitlines()):
                # trim '*' or '-' bullet points
                rls_note_line = _\
                    .chain(rls_note_line)\
                    .trim()\
                    .reg_exp_replace(r'^\* ', '')\
                    .reg_exp_replace(r'^- ', '')\
                    .trim()\
                    .value()

                if not rls_note_line:
                    continue
                if i == 0:
                    bullet_points.append(
                        self._build_bullet_point_head(
                            line=rls_note_line,
                            tag=tag,
                            rls_note_obj=rls_note_obj
                        )
                    )
                else:
                    bullet_points.append(self._build_sub_bullet_point(rls_note_line))
        return bullet_points

    def _build_bullet_point_head(
        self,
        line: str,
        tag: str,
        rls_note_obj: ReleaseNote
    ) -> str:
        """returns the headline of a bullet point, usually containing some meta information
        e.g. '* foo-message (#pr-number, @foo-user)' """
        pass

    def _build_sub_bullet_point(self, rls_note_line: str):
        """returns the details of a bullet point, usually as indented bullet point"""
        pass

    @abstractmethod
    def _title(
        self,
        node: TitleNode,
        level: int
    ) -> str:
        pass

    @abstractmethod
    def _generate_link(self, rls_note_obj: ReleaseNote) -> bool:
        pass

    @abstractmethod
    def _build_link(self, url: str, text) -> str:
        pass


class MarkdownRenderer(Renderer):
    def __init__(
        self,
        release_note_objs: [ReleaseNote],
        force_link_generation:bool=False
    ):
        super().__init__(release_note_objs)
        self.force_link_generation = force_link_generation

    def _title(
        self,
        node: TitleNode,
        level: int
    ) -> str:
        return '{hashtags} {title}'.format(hashtags=_.repeat('#', level),title=node.title)

    def _generate_link(self, rls_note_obj: ReleaseNote) -> bool:
        return self.force_link_generation or not rls_note_obj.from_same_github_instance

    def _build_bullet_point_head(
        self,
        line: str,
        tag: str,
        rls_note_obj: ReleaseNote
    ) -> str:
        header_suffix = self._header_suffix(rls_note_obj)

        return '* *[{tag}]* {rls_note_line}{header_suffix}'.format(
            tag=tag,
            rls_note_line=line,
            header_suffix=header_suffix
        )

    def _build_sub_bullet_point(self, rls_note_line: str):
        return '  * {rls_note_line}'.format(rls_note_line=rls_note_line)

    def _build_link(self, url: str, text) -> str:
        return '[{text}]({url})'.format(
            url=url,
            text=text
        )

# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import unittest

from github.release_notes.model import (
    REF_TYPE_PULL_REQUEST,
    REF_TYPE_COMMIT,
)
from github.release_notes.renderer import (
    MarkdownRenderer,
    get_or_call,
    CATEGORY_ACTION_ID,
    CATEGORY_NOTEWORTHY_ID,
    CATEGORY_IMPROVEMENT_ID,
    TARGET_GROUP_USER_ID,
    TARGET_GROUP_OPERATOR_ID,
    TARGET_GROUP_DEVELOPER_ID,
)
from test.github.release_notes.default_util import (
    release_note_block_with_defaults,
    CURRENT_REPO_NAME,
)


class RendererTest(unittest.TestCase):

    def test_render_multiline_rls_note_should_have_2nd_level_bullet_points(self):
        multiline_text = \
        'first line with header\n'\
        'second line\n'\
        'third line\n'
        release_note_objs = [
            release_note_block_with_defaults(
                text=multiline_text,
            )
        ]
        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* first line with header (#42, @foo)\n'\
            '  * second line\n'\
            '  * third line'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_from_other_github_should_auto_link(self):
        release_note_objs = [
            release_note_block_with_defaults(
                source_repo='madeup.enterprise.github.corp/o/s',
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '\n'.join((
                '# [s]',
                '## Improvements',
                '* *[USER]* default release note text '
                '([o/s#42](https://madeup.enterprise.github.corp/o/s/pull/42), '
                '[@foo](https://madeup.enterprise.github.corp/foo))'
            ))
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_reference_pr(self):
        release_note_objs = [
            release_note_block_with_defaults(
                reference_type=REF_TYPE_PULL_REQUEST,
                reference_id='42',
                source_repo=CURRENT_REPO_NAME,
            ),
            release_note_block_with_defaults(
                reference_type=REF_TYPE_PULL_REQUEST,
                reference_id='1',
                text='other component, same github instance rls note',
                source_repo='github.com/madeup/a-foo-bar',
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* default release note text (#42, @foo)\n'\
            '# [a-foo-bar]\n'\
            '## Improvements\n'\
            '* *[USER]* other component, same github instance rls note (madeup/a-foo-bar#1, @foo)'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_reference_commit(self):
        release_note_objs = [
            release_note_block_with_defaults(
                text='rls note 1',
                reference_type=REF_TYPE_COMMIT,
                reference_id='commit-id-1',
                source_repo=CURRENT_REPO_NAME,
            ),
            # As the source repository is on the same github instance as the current repository
            # it can be auto linked by github, hence we do not need to build a link to the commit
            # with the cut off commit id as link text
            release_note_block_with_defaults(
                text='other component rls note',
                reference_type=REF_TYPE_COMMIT,
                reference_id='very-long-commit-id-that-will-not-be-shortened',
                user_login='bar',
                source_repo='github.com/madeup/a-foo-bar',
            ),
            # the source repository is on a different github instance as the current repository.
            # It can not be auto linked by github, hence we need to build a link to the commit
            # with the cut off commit id as link text
            release_note_block_with_defaults(
                text='release note from different github instance',
                reference_type=REF_TYPE_COMMIT,
                reference_id='very-long-commit-id-that-will-be-shortened',
                user_login='bar',
                source_repo='madeup.enterprise.github.corp/o/s',
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = ''\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* rls note 1 (commit-id-1, @foo)\n'\
            '# [a-foo-bar]\n'\
            '## Improvements\n'\
            '* *[USER]* other component rls note ' \
            '(madeup/a-foo-bar@very-long-commit-id-that-will-not-be-shortened, @bar)\n'\
            '# [s]\n'\
            '## Improvements\n'\
            '* *[USER]* release note from different github instance ' \
            '([o/s@very-long-co](https://madeup.enterprise.github.corp/o/s/commit/'\
            'very-long-commit-id-that-will-be-shortened), '\
            '[@bar](https://madeup.enterprise.github.corp/bar))'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_user(self):
        release_note_objs = [
            release_note_block_with_defaults(
                reference_type=None,
                reference_id=None,
                user_login='bar',
                source_repo='github.com/madeup/a-foo-bar',
            ),
            release_note_block_with_defaults(
                reference_type=None,
                reference_id=None,
                user_login='foo',
                source_repo=CURRENT_REPO_NAME,
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* default release note text (@foo)\n'\
            '# [a-foo-bar]\n'\
            '## Improvements\n'\
            '* *[USER]* default release note text (@bar)'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_no_reference_no_user(self):
        release_note_objs = [
            release_note_block_with_defaults(
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/madeup/a-foo-bar',
            ),
            release_note_block_with_defaults(
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo=CURRENT_REPO_NAME,
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* default release note text\n'\
            '# [a-foo-bar]\n'\
            '## Improvements\n'\
            '* *[USER]* default release note text'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_no_release_notes(self):
        release_note_objs = []

        expected_md_str = 'no release notes available'
        self.assertEqual(
            expected_md_str,
            MarkdownRenderer(release_note_objs=release_note_objs).render()
        )

    def test_render_skip_empty_lines(self):
        release_note_objs = [
            release_note_block_with_defaults(
                text='first line1\n\n second line1', #empty line
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
            release_note_block_with_defaults(
                text='first line2\n \nsecond line2', #empty line with space
                reference_type=None,
                reference_id=None,
                user_login=None,
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* first line1\n'\
            '  * second line1\n'\
            '* *[USER]* first line2\n'\
            '  * second line2'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_remove_bullet_points(self):
        release_note_objs = [
            release_note_block_with_defaults(
                text='first line1\n* second line1', #contains bullet point (*)
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
            release_note_block_with_defaults(
                text='first line2\n  * second line2',  # contains bullet point with extra spaces
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
            release_note_block_with_defaults(
                text='- first line3\n  - second line3',  # contains bullet point (-)
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
            release_note_block_with_defaults(
                text='first line4\n*italic*',  # no bullet point, just italic
                reference_type=None,
                reference_id=None,
                user_login=None,
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* first line1\n'\
            '  * second line1\n'\
            '* *[USER]* first line2\n'\
            '  * second line2\n'\
            '* *[USER]* first line3\n'\
            '  * second line3\n'\
            '* *[USER]* first line4\n'\
            '  * *italic*'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_categories(self):
        release_note_objs = [
            release_note_block_with_defaults(
                category_id=CATEGORY_IMPROVEMENT_ID,
                text='improvement release note',
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
            release_note_block_with_defaults(
                category_id=CATEGORY_NOTEWORTHY_ID,
                text='noteworthy release note',
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
            release_note_block_with_defaults(
                category_id=CATEGORY_ACTION_ID,
                text='action required release note',
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [current-repo]\n'\
            '## Action Required\n'\
            '* *[USER]* action required release note\n'\
            '## Most notable changes\n'\
            '* *[USER]* noteworthy release note\n'\
            '## Improvements\n'\
            '* *[USER]* improvement release note'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_target_group(self):
        release_note_objs = [
            release_note_block_with_defaults(
                target_group_id=TARGET_GROUP_USER_ID,
                text='user release note',
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
            release_note_block_with_defaults(
                target_group_id=TARGET_GROUP_OPERATOR_ID,
                text='operator release note',
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
            release_note_block_with_defaults(
                target_group_id=TARGET_GROUP_DEVELOPER_ID,
                text='developer release note',
                reference_type=None,
                reference_id=None,
                user_login=None,
            ),
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* user release note\n'\
            '* *[OPERATOR]* operator release note\n'\
            '* *[DEVELOPER]* developer release note'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_get_or_call(self):
        def call_me():
            return 'value'

        self.assertEqual('value', get_or_call({'key': 'value'}, 'key'))
        self.assertEqual('value', get_or_call({'key': lambda: 'value'}, 'key'))
        self.assertEqual('value', get_or_call({'key': {'subkey': call_me}}, 'key.subkey'))
        self.assertEqual(None, None)

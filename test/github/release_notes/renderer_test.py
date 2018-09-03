# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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
from pydash import _

from github.release_notes.model import (
    ReleaseNoteBlock,
    ref_type_pull_request,
    ref_type_commit
)
from github.release_notes.renderer import (
    MarkdownRenderer,
    get_or_call
)
from product.model import ComponentName


class RendererTest(unittest.TestCase):
    def setUp(self):
        self.cn_current_repo = ComponentName('github.com/gardener/current-repo')

    def test_render_multiline_rls_note(self):
        multiline_text = \
        'first line with header\n'\
        'second line\n'\
        'third line\n'
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text=multiline_text,
                reference_type=ref_type_pull_request,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
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

    def test_render_from_other_github(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='from other github instance',
                reference_type=ref_type_pull_request,
                reference_id='42',
                user_login='foo',
                source_repo='madeup.enterprise.github.corp/o/s',
                cn_current_repo=self.cn_current_repo
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '\n'.join((
                '# [s]',
                '## Improvements',
                '* *[USER]* from other github instance '
                '([o/s#42](https://madeup.enterprise.github.corp/o/s/pull/42), '
                '[@foo](https://madeup.enterprise.github.corp/foo))'
            ))
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_reference_pr(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='rls note 1',
                reference_type=ref_type_pull_request,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='other component rls note',
                reference_type=ref_type_pull_request,
                reference_id='1',
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* other component rls note (gardener/a-foo-bar#1, @bar)\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* rls note 1 (#42, @foo)'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_reference_commit(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='rls note 1',
                reference_type=ref_type_commit,
                reference_id='commit-id-1',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='other component rls note',
                reference_type=ref_type_commit,
                reference_id='very-long-commit-id-that-will-not-be-shortened-in-md',
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='release note from different github instance',
                reference_type=ref_type_commit,
                reference_id='very-long-commit-id-that-will-be-shortened',
                user_login='bar',
                source_repo='madeup.enterprise.github.corp/o/s',
                cn_current_repo=self.cn_current_repo
            )
        ]

        self.maxDiff = None
        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = ''\
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* other component rls note ' \
            '(gardener/a-foo-bar@very-long-commit-id-that-will-not-be-shortened-in-md, @bar)\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* rls note 1 (commit-id-1, @foo)\n'\
            '# [s]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* release note from different github instance ' \
            '([o/s@very-long-co](https://madeup.enterprise.github.corp/o/s/commit/'\
            'very-long-commit-id-that-will-be-shortened), '\
            '[@bar](https://madeup.enterprise.github.corp/bar))'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_user(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='no source repo reference',
                reference_type=None,
                reference_id=None,
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='no reference',
                reference_type=None,
                reference_id=None,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* no source repo reference (@bar)\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* no reference (@foo)'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_no_reference_no_user(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='no source repo reference no user',
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='no reference no user',
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* no source repo reference no user\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* no reference no user'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_no_release_notes(self):
        release_note_objs = []

        expected_md_str = 'no release notes available'
        self.assertEqual(
            expected_md_str,
            MarkdownRenderer(release_note_objs=release_note_objs).render()
        )

    def test_render_no_skip_empty_lines(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='first line1\n\n second line1', #empty line
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='first line2\n \nsecond line2', #empty line with space
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* first line1\n'\
            '  * second line1\n'\
            '* *[OPERATOR]* first line2\n'\
            '  * second line2'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_render_remove_bullet_points(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='first line1\n* second line1', #contains bullet point (*)
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='first line2\n  * second line2',  # contains bullet point with extra spaces
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='- first line3\n  - second line3',  # contains bullet point (-)
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='first line4\n*italic*',  # no bullet point, just italic
                reference_type=None,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            )
        ]

        actual_md_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_md_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* first line1\n'\
            '  * second line1\n'\
            '* *[OPERATOR]* first line2\n'\
            '  * second line2\n'\
            '* *[OPERATOR]* first line3\n'\
            '  * second line3\n'\
            '* *[OPERATOR]* first line4\n'\
            '  * *italic*'
        self.assertEqual(expected_md_str, actual_md_str)

    def test_get_or_call(self):
        def call_me():
            return 'value'

        self.assertEqual('value', get_or_call({'key': 'value'}, 'key'))
        self.assertEqual('value', get_or_call({'key': lambda: 'value'}, 'key'))
        self.assertEqual('value', get_or_call({'key': {'subkey': call_me}}, 'key.subkey'))
        self.assertEqual(None, None)

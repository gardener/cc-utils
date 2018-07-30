# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from github.release_notes import (
    extract_release_notes,
    create_release_note_obj,
    MarkdownRenderer
)

class ReleaseNotesTest(unittest.TestCase):
    def test_rls_note_extraction_improvement(self):
        text = \
            '``` improvement user\n'\
            'this is a release note text\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo',
            current_repo='github.com/gardener/current-repo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text='this is a release note text',
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_ignore_noise_in_header(self):
        def verify_noise_ignored(text):
            release_notes = extract_release_notes(
                pr_number=42,
                text=text,
                user_login='foo',
                current_repo='github.com/s/repo'
            )

            self.assertEqual(1, len(release_notes))
            self.assertEqual(
                create_release_note_obj(
                    category_id='improvement',
                    target_group_id='user',
                    text='rlstext',
                    reference_is_pr=True,
                    reference_id=42,
                    user_login='foo',
                    source_repo='github.com/s/repo',
                    is_current_repo=True
                ),
                _.nth(release_notes, 0)
            )
        text = \
            '``` improvement user \n'\
            'rlstext\n'\
            '```'
        verify_noise_ignored(text)

        text = \
            '``` improvement user     \n'\
            'rlstext\n'\
            '```'
        verify_noise_ignored(text)

        text = \
            '``` improvement user this is some noise that should be ignored\n'\
            'rlstext\n'\
            '```'
        verify_noise_ignored(text)


    def test_rls_note_extraction_noteworthy(self):
        text = \
            '``` noteworthy operator\n'\
            'notew-text\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo',
            current_repo='github.com/gardener/current-repo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            create_release_note_obj(
                category_id='noteworthy',
                target_group_id='operator',
                text='notew-text',
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_src_repo(self):
        def source_repo_test(
            code_block,
            exp_ref_id,
            exp_usr,
            exp_text,
            exp_ref_is_pr=True
        ):
            release_notes = extract_release_notes(
                pr_number=42,
                text=code_block,
                user_login='pr-transport-user',
                current_repo='github.com/gardener/current-repo'
            )
            self.assertEqual(1, len(release_notes))
            self.assertEqual(
                create_release_note_obj(
                    category_id='improvement',
                    target_group_id='user',
                    text=exp_text,
                    reference_is_pr=exp_ref_is_pr,
                    reference_id=exp_ref_id,
                    user_login=exp_usr,
                    source_repo='github.com/gardener/source-component',
                    is_current_repo=False
                ),
                _.nth(release_notes, 0)
            )

        code_block = \
            '``` improvement user github.com/gardener/source-component #1 @original-user-foo\n'\
            'source repo, pr refid and user\n'\
            '```'
        source_repo_test(code_block, exp_ref_id=1, exp_usr='original-user-foo', exp_text='source repo, pr refid and user')

        code_block = \
            '``` improvement user github.com/gardener/source-component $commit-id @original-user-foo\n'\
            'source repo, commit refid and user\n'\
            '```'
        source_repo_test(code_block, exp_ref_id='commit-id', exp_ref_is_pr=False, exp_usr='original-user-foo', exp_text='source repo, commit refid and user')

        code_block = \
            '``` improvement user github.com/gardener/source-component #1 @original-user-foo some random noise\n'\
            'noise test\n'\
            '```'
        source_repo_test(code_block, exp_ref_id=1, exp_usr='original-user-foo', exp_text='noise test')

        code_block = \
            '``` improvement user github.com/gardener/source-component #1 some random noise\n'\
            'no user specified\n'\
            '```'
        source_repo_test(code_block, exp_ref_id=1, exp_usr=None, exp_text='no user specified')

        code_block = \
            '``` improvement user github.com/gardener/source-component @user some random noise\n'\
            'no pull request ref_id specified\n'\
            '```'
        source_repo_test(code_block, exp_ref_id=None, exp_ref_is_pr=False, exp_usr='user', exp_text='no pull request ref_id specified')

        code_block = \
            '``` improvement user github.com/gardener/source-component\n'\
            'source_repo only\n'\
            '```'
        source_repo_test(code_block, exp_ref_id=None, exp_ref_is_pr=False, exp_usr=None, exp_text='source_repo only')

        code_block = \
            '``` improvement user github.com/gardener/source-component some random noise\n'\
            'source_repo only - with noise\n'\
            '```'
        source_repo_test(code_block, exp_ref_id=None, exp_ref_is_pr=False, exp_usr=None, exp_text='source_repo only - with noise')


    def test_multiple_rls_note_extraction(self):
        text = \
            'random text\n'\
            '``` improvement user\n'\
            'imp-user-text\n'\
            '```\n'\
            '``` improvement operator\r\n'\
            'imp-op-text with carriage return and newline feed\r\n'\
            '```\r\n'\
            '``` noteworthy operator\n'\
            'notew-text\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo',
            current_repo='github.com/gardener/current-repo'
        )

        self.assertEqual(3, len(release_notes))
        self.assertEqual(
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text='imp-user-text',
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            _.nth(release_notes, 0)
        )
        self.assertEqual(
            create_release_note_obj(
                category_id='improvement',
                target_group_id='operator',
                text='imp-op-text with carriage return and newline feed',
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            _.nth(release_notes, 1)
        )
        self.assertEqual(
            create_release_note_obj(
                category_id='noteworthy',
                target_group_id='operator',
                text='notew-text',
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            _.nth(release_notes, 2)
        )

    def test_rls_note_extraction_multiple_lines(self):
        text = \
            '``` improvement user\n'\
            'first line\n'\
            'second line\r\n'\
            'third line\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo',
            current_repo='github.com/gardener/current-repo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text='first line\nsecond line\r\nthird line',
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_trim_text(self):
        text = \
            '``` improvement user \n'\
            '\n'\
            '        text with spaces      '\
            '\n'\
            '\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo',
            current_repo='github.com/gardener/current-repo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text='text with spaces',
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_no_release_notes(self):
        def verify_no_release_note(text: str):
            release_notes = extract_release_notes(
                pr_number=42,
                text=text,
                user_login='foo',
                current_repo='github.com/gardener/current-repo'
            )
            self.assertEqual(0, len(release_notes))

        text = \
            '``` improvement user\n'\
            '\n'\
            '```'
        verify_no_release_note(text)

        text = \
            '``` improvement user\n'\
            ' NONE \n'\
            '```'
        verify_no_release_note(text)

        text = \
            '``` improvement user\n'\
            'none\n'\
            '```'
        verify_no_release_note(text)

        text = \
            '``` improvement user\n'\
            '```'
        verify_no_release_note(text)

        text = \
            '``` improvement\n'\
            'required target_group is missing in code block header\n'\
            '```'
        verify_no_release_note(text)

        text = 'some random description'
        verify_no_release_note(text)

    def test_markdown_multiline_rls_note(self):
        multiline_text = \
        'first line with header\n'\
        'second line\n'\
        'third line\n'
        release_note_objs = [
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text=multiline_text,
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* first line with header (#42, [@foo](https://github.com/foo))\n'\
            '  * second line\n'\
            '  * third line'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_pr(self):
        release_note_objs = [
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text='rls note 1',
                reference_is_pr=True,
                reference_id=42,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            create_release_note_obj(
                category_id='noteworthy',
                target_group_id='operator',
                text='other component rls note',
                reference_is_pr=True,
                reference_id=1,
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                is_current_repo=False
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* other component rls note ([gardener/a-foo-bar#1](https://github.com/gardener/a-foo-bar/pull/1), [@bar](https://github.com/bar))\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* rls note 1 (#42, [@foo](https://github.com/foo))'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_commit(self):
        release_note_objs = [
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text='rls note 1',
                reference_is_pr=False,
                reference_id='commit-id-1',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            ),
            create_release_note_obj(
                category_id='noteworthy',
                target_group_id='operator',
                text='other component rls note',
                reference_is_pr=False,
                reference_id='commit-id-2',
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                is_current_repo=False
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* other component rls note ([gardener/a-foo-bar@commit-id-2](https://github.com/gardener/a-foo-bar/commit/commit-id-2), [@bar](https://github.com/bar))\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* rls note 1 (commit-id-1, [@foo](https://github.com/foo))'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_source_repo_user(self):
        release_note_objs = [
            create_release_note_obj(
                category_id='improvement',
                target_group_id='operator',
                text='no source repo user',
                reference_is_pr=True,
                reference_id=42,
                user_login=None,
                source_repo='github.com/s/repo',
                is_current_repo=False
            ),
            create_release_note_obj(
                category_id='improvement',
                target_group_id='operator',
                text='no user',
                reference_is_pr=True,
                reference_id=1,
                user_login=None,
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()
        expected_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[OPERATOR]* no user (#1)\n'\
            '# [repo]\n'\
            '## Improvements\n'\
            '* *[OPERATOR]* no source repo user ([s/repo#42](https://github.com/s/repo/pull/42))'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_no_reference(self):
        release_note_objs = [
            create_release_note_obj(
                category_id='noteworthy',
                target_group_id='operator',
                text='no source repo reference',
                reference_is_pr=False,
                reference_id=None,
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                is_current_repo=False
            ),
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text='no reference',
                reference_is_pr=False,
                reference_id=None,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* no source repo reference ([@bar](https://github.com/bar))\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* no reference ([@foo](https://github.com/foo))'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_no_reference_no_user(self):
        release_note_objs = [
            create_release_note_obj(
                category_id='noteworthy',
                target_group_id='operator',
                text='no source repo reference no user',
                reference_is_pr=False,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                is_current_repo=False
            ),
            create_release_note_obj(
                category_id='improvement',
                target_group_id='user',
                text='no reference no user',
                reference_is_pr=False,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/current-repo',
                is_current_repo=True
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* no source repo reference no user\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* no reference no user'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_no_release_notes(self):
        release_note_objs = []

        expected_str = 'no release notes available'
        self.assertEqual(expected_str, MarkdownRenderer(release_note_objs=release_note_objs).render())

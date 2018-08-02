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
    pr_number_from_message,
    ReleaseNoteBlock,
    MarkdownRenderer,
    release_note_objs_to_block_str
)
from model.base import ModelValidationError
from product.model import ComponentName

class ReleaseNotesTest(unittest.TestCase):
    def setUp(self):
        self.cn_current_repo = ComponentName('github.com/gardener/current-repo')

    def test_rls_note_extraction_improvement(self):
        text = \
            '``` improvement user\n'\
            'this is a release note text\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number='42',
            text=text,
            user_login='foo',
            cn_current_repo=self.cn_current_repo
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='this is a release note text',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            ),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_ignore_noise_in_header(self):
        def verify_noise_ignored(text):
            release_notes = extract_release_notes(
                pr_number='42',
                text=text,
                user_login='foo',
                cn_current_repo=self.cn_current_repo
            )

            self.assertEqual(1, len(release_notes))
            self.assertEqual(
                ReleaseNoteBlock(
                    category_id='improvement',
                    target_group_id='user',
                    text='rlstext',
                    reference_is_pr=True,
                    reference_id='42',
                    user_login='foo',
                    source_repo='github.com/gardener/current-repo',
                    cn_current_repo=self.cn_current_repo
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
            pr_number='42',
            text=text,
            user_login='foo',
            cn_current_repo=self.cn_current_repo
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='notew-text',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
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
                pr_number='42',
                text=code_block,
                user_login='pr-transport-user',
                cn_current_repo=self.cn_current_repo
            )
            self.assertEqual(1, len(release_notes))
            self.assertEqual(
                ReleaseNoteBlock(
                    category_id='improvement',
                    target_group_id='user',
                    text=exp_text,
                    reference_is_pr=exp_ref_is_pr,
                    reference_id=exp_ref_id,
                    user_login=exp_usr,
                    source_repo='github.com/gardener/source-component',
                    cn_current_repo=self.cn_current_repo
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
            pr_number='42',
            text=text,
            user_login='foo',
            cn_current_repo=self.cn_current_repo
        )

        self.assertEqual(3, len(release_notes))
        self.assertEqual(
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='imp-user-text',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            ),
            _.nth(release_notes, 0)
        )
        self.assertEqual(
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='operator',
                text='imp-op-text with carriage return and newline feed',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            ),
            _.nth(release_notes, 1)
        )
        self.assertEqual(
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='notew-text',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
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
            pr_number='42',
            text=text,
            user_login='foo',
            cn_current_repo=self.cn_current_repo
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='first line\nsecond line\r\nthird line',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
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
            pr_number='42',
            text=text,
            user_login='foo',
            cn_current_repo=self.cn_current_repo
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='text with spaces',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            ),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_no_release_notes(self):
        def verify_no_release_note(text: str):
            release_notes = extract_release_notes(
                pr_number='42',
                text=text,
                user_login='foo',
                cn_current_repo=self.cn_current_repo
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
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text=multiline_text,
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* first line with header (#42, @foo)\n'\
            '  * second line\n'\
            '  * third line'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_from_other_github(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='from other github instance',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='madeup.enterprise.github.corp/o/s',
                cn_current_repo=self.cn_current_repo
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [s]\n'\
            '## Improvements\n'\
            '* *[USER]* from other github instance ([o/s#42](https://madeup.enterprise.github.corp/o/s/pull/42), [@foo](https://madeup.enterprise.github.corp/foo))'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_reference_pr(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='rls note 1',
                reference_is_pr=True,
                reference_id='42',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='other component rls note',
                reference_is_pr=True,
                reference_id=1,
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* other component rls note (gardener/a-foo-bar#1, @bar)\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* rls note 1 (#42, @foo)'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_reference_commit(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='rls note 1',
                reference_is_pr=False,
                reference_id='commit-id-1',
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='other component rls note',
                reference_is_pr=False,
                reference_id='commit-id-2',
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = ''\
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* other component rls note (gardener/a-foo-bar@commit-id-2, @bar)\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* rls note 1 (commit-id-1, @foo)'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_user(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='no source repo reference',
                reference_is_pr=False,
                reference_id=None,
                user_login='bar',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='no reference',
                reference_is_pr=False,
                reference_id=None,
                user_login='foo',
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
            )
        ]
        actual_str = MarkdownRenderer(release_note_objs=release_note_objs).render()

        expected_str = \
            '# [a-foo-bar]\n'\
            '## Most notable changes\n'\
            '* *[OPERATOR]* no source repo reference (@bar)\n'\
            '# [current-repo]\n'\
            '## Improvements\n'\
            '* *[USER]* no reference (@foo)'
        self.assertEqual(expected_str, actual_str)

    def test_markdown_no_reference_no_user(self):
        release_note_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='no source repo reference no user',
                reference_is_pr=False,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='no reference no user',
                reference_is_pr=False,
                reference_id=None,
                user_login=None,
                source_repo='github.com/gardener/current-repo',
                cn_current_repo=self.cn_current_repo
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

    def test_rls_note_obj_to_block_str(self):
        try:
            rn_block = ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='no source repo, no reference, no user',
                reference_is_pr=False,
                reference_id='commit-id',
                user_login='foo',
                source_repo=None,
                cn_current_repo=self.cn_current_repo
            )
            self.fail('a ReleaseNoteBlock always has a source repository, even if it points to the "current" repository')
        except RuntimeError:
            pass


        rn_block = ReleaseNoteBlock(
            category_id='noteworthy',
            target_group_id='operator',
            text='no reference, no user',
            reference_is_pr=False,
            reference_id=None,
            user_login=None,
            source_repo='github.com/gardener/a-foo-bar',
            cn_current_repo=self.cn_current_repo
        )
        expected = \
        '``` noteworthy operator github.com/gardener/a-foo-bar\n'\
        'no reference, no user'\
        '\n```'
        self.assertEqual(expected, rn_block.to_block_str())

        rn_block = ReleaseNoteBlock(
            category_id='noteworthy',
            target_group_id='operator',
            text='no reference',
            reference_is_pr=False,
            reference_id=None,
            user_login='foo',
            source_repo='github.com/gardener/a-foo-bar',
            cn_current_repo=self.cn_current_repo
        )
        expected = \
        '``` noteworthy operator github.com/gardener/a-foo-bar @foo\n'\
        'no reference'\
        '\n```'
        self.assertEqual(expected, rn_block.to_block_str())

        rn_block = ReleaseNoteBlock(
            category_id='noteworthy',
            target_group_id='operator',
            text='no user; reference is PR',
            reference_is_pr=True,
            reference_id='42',
            user_login=None,
            source_repo='github.com/gardener/a-foo-bar',
            cn_current_repo=self.cn_current_repo
        )
        expected = \
        '``` noteworthy operator github.com/gardener/a-foo-bar #42\n'\
        'no user; reference is PR'\
        '\n```'
        self.assertEqual(expected, rn_block.to_block_str())

        rn_block = ReleaseNoteBlock(
            category_id='noteworthy',
            target_group_id='operator',
            text='reference is commit',
            reference_is_pr=False,
            reference_id='commit-id',
            user_login='foo',
            source_repo='github.com/gardener/a-foo-bar',
            cn_current_repo=self.cn_current_repo
        )
        expected = \
        '``` noteworthy operator github.com/gardener/a-foo-bar $commit-id @foo\n'\
        'reference is commit'\
        '\n```'
        self.assertEqual(expected, rn_block.to_block_str())

    def test_release_note_objs_to_block_str(self):
        rn_objs = []
        expected = ''
        self.assertEqual(expected, release_note_objs_to_block_str(rn_objs))

        rn_objs = None
        expected = ''
        self.assertEqual(expected, release_note_objs_to_block_str(rn_objs))

        rn_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='test with one release note object',
                reference_is_pr=False,
                reference_id='commit-id',
                user_login='foo',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            )
        ]
        expected = \
        '``` noteworthy operator github.com/gardener/a-foo-bar $commit-id @foo\n'\
        'test with one release note object'\
        '\n```'
        self.assertEqual(expected, release_note_objs_to_block_str(rn_objs))

        rn_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='test with multiple release note objects',
                reference_is_pr=False,
                reference_id='commit-id',
                user_login='foo',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='another one',
                reference_is_pr=False,
                reference_id='commit-id',
                user_login='foo',
                source_repo='github.com/s/repo',
                cn_current_repo=self.cn_current_repo
            ),
        ]
        expected = \
        '``` noteworthy operator github.com/gardener/a-foo-bar $commit-id @foo\n'\
        'test with multiple release note objects'\
        '\n```'\
        '\n'\
        '\n'\
        '``` noteworthy operator github.com/s/repo $commit-id @foo\n'\
        'another one'\
        '\n```'
        self.assertEqual(expected, release_note_objs_to_block_str(rn_objs))

    def test_pr_number_from_message(self):
        self.assertEqual('42', pr_number_from_message('Merge pull request #42'))
        self.assertEqual('42', pr_number_from_message('Merge pull request #42 Merge pull request #79'))
        self.assertEqual('42', pr_number_from_message('Merge pull request #42 some text'))
        self.assertEqual('42', pr_number_from_message('Merge pull request #42\nsome text'))
        self.assertEqual('1', pr_number_from_message('Squash commit (#1)'))

        self.assertIsNone(pr_number_from_message('not supported format #42'))
        self.assertIsNone(pr_number_from_message('some commit'))


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
    Commit,
    ref_type_pull_request,
    ref_type_commit
)
from github.release_notes.util import (
    ReleaseNotes,
    extract_release_notes,
    pr_number_from_subject,
    commits_from_logs,
    fetch_release_notes_from_commits
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
            reference_id='42',
            reference_type=ref_type_pull_request,
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
                reference_type=ref_type_pull_request,
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
                reference_id='42',
                reference_type=ref_type_pull_request,
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
                    reference_type=ref_type_pull_request,
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
            reference_id='42',
            reference_type=ref_type_pull_request,
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
                reference_type=ref_type_pull_request,
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
            exp_ref_type=ref_type_pull_request
        ):
            release_notes = extract_release_notes(
                reference_id='42',
                reference_type=ref_type_pull_request,
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
                    reference_type=exp_ref_type,
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
        source_repo_test(
            code_block,
            exp_ref_id=1,
            exp_usr='original-user-foo',
            exp_text='source repo, pr refid and user'
        )

        code_block = \
'''``` improvement user github.com/gardener/source-component $commit-id @original-user-foo
source repo, commit refid and user
```'''
        source_repo_test(
            code_block,
            exp_ref_id='commit-id',
            exp_ref_type=ref_type_commit,
            exp_usr='original-user-foo',
            exp_text='source repo, commit refid and user'
        )

        code_block = \
'''``` improvement user github.com/gardener/source-component #1 @original-user-foo some random noise
noise test
```'''
        source_repo_test(
            code_block,
            exp_ref_id=1,
            exp_usr='original-user-foo',
            exp_text='noise test'
        )

        code_block = \
'''``` improvement user github.com/gardener/source-component #1 some random noise
no user specified
```'''
        source_repo_test(code_block, exp_ref_id=1, exp_usr=None, exp_text='no user specified')

        code_block = \
            '``` improvement user github.com/gardener/source-component @user some random noise\n'\
            'no pull request ref_id specified\n'\
            '```'
        source_repo_test(
            code_block,
            exp_ref_id=None,
            exp_ref_type=None,
            exp_usr='user',
            exp_text='no pull request ref_id specified'
        )

        code_block = \
'''``` improvement user github.com/gardener/source-component
source_repo only
```'''
        source_repo_test(
            code_block,
            exp_ref_id=None,
            exp_ref_type=None,
            exp_usr=None,
            exp_text='source_repo only'
        )

        code_block = \
'''``` improvement user github.com/gardener/source-component some random noise
source_repo only - with noise
```'''
        source_repo_test(
            code_block,
            exp_ref_id=None,
            exp_ref_type=None,
            exp_usr=None,
            exp_text='source_repo only - with noise'
        )

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
            reference_id='42',
            reference_type=ref_type_pull_request,
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
                reference_type=ref_type_pull_request,
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
                reference_type=ref_type_pull_request,
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
                reference_type=ref_type_pull_request,
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
            reference_id='42',
            reference_type=ref_type_pull_request,
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
                reference_type=ref_type_pull_request,
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
            reference_id='42',
            reference_type=ref_type_pull_request,
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
                reference_type=ref_type_pull_request,
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
                reference_id='42',
                reference_type=ref_type_pull_request,
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

    def test_rls_note_obj_to_block(self):
        try:
            rn_block = ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='no source repo, no reference, no user',
                reference_type=ref_type_commit,
                reference_id='commit-id',
                user_login='foo',
                source_repo=None,
                cn_current_repo=self.cn_current_repo
            )
            self.fail(
                'a ReleaseNoteBlock always has a source repository, '
                'even if it points to the "current" repository'
            )
        except RuntimeError:
            pass

        rn_block = ReleaseNoteBlock(
            category_id='noteworthy',
            target_group_id='operator',
            text='no reference, no user',
            reference_type=ref_type_commit,
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
            reference_type=ref_type_commit,
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
            reference_type=ref_type_pull_request,
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
            reference_type=ref_type_commit,
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
        self.assertEqual(expected, ReleaseNotes(rn_objs).release_note_blocks())

        rn_objs = None
        expected = ''
        self.assertEqual(expected, ReleaseNotes(rn_objs).release_note_blocks())

        rn_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='test with one release note object',
                reference_type=ref_type_commit,
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
        self.assertEqual(expected, ReleaseNotes(rn_objs).release_note_blocks())

        rn_objs = [
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='test with multiple release note objects',
                reference_type=ref_type_commit,
                reference_id='commit-id',
                user_login='foo',
                source_repo='github.com/gardener/a-foo-bar',
                cn_current_repo=self.cn_current_repo
            ),
            ReleaseNoteBlock(
                category_id='noteworthy',
                target_group_id='operator',
                text='another one',
                reference_type=ref_type_commit,
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
        self.assertEqual(expected, ReleaseNotes(rn_objs).release_note_blocks())

    def test_pr_number_from_subject(self):
        self.assertEqual('42', pr_number_from_subject('Merge pull request #42'))
        self.assertEqual(
            '42',
            pr_number_from_subject('Merge pull request #42 Merge pull request #79')
        )
        self.assertEqual('42', pr_number_from_subject('Merge pull request #42 some text'))
        self.assertEqual('42', pr_number_from_subject('Merge pull request #42\nsome text'))
        self.assertEqual('1', pr_number_from_subject('Squash commit (#1)'))

        self.assertIsNone(pr_number_from_subject('not supported format #42'))
        self.assertIsNone(pr_number_from_subject('some commit'))

    def test_commits_from_logs(self):
        logs = []
        self.assertEqual([], commits_from_logs(logs))

        logs = [
            'commit-id1\x00subject1\x00message1',
            '\ncommit-id2\x00subject2\x00message2',
            '\n',
            'random text'
        ]
        actual_commits = commits_from_logs(logs)
        expected_commits = [
            Commit(hash='commit-id1', subject='subject1', message='message1'),
            Commit(hash='commit-id2', subject='subject2', message='message2'),
        ]
        self.assertEqual(expected_commits, actual_commits)

    def test_fetch_release_notes_from_commits(self):
        commits = [
            Commit(hash='commit-id1', subject='subject1', message='message1'),
            Commit(
                hash='commit-id2',
                subject='subject2',
                message='```improvement user\nrelease note text in commit\n```'
            ),
            Commit(
                hash='commit-id3',
                subject='subject2',
                message='foo\n```improvement user\nrelease note text in commit 2\n```\nbar'
            )
        ]
        actual_rn_objs = fetch_release_notes_from_commits(commits, self.cn_current_repo)
        expected_rn_objs = [
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='release note text in commit',
                reference_type=ref_type_commit,
                reference_id='commit-id2',
                user_login=None,
                source_repo=self.cn_current_repo.name(),
                cn_current_repo=self.cn_current_repo),
            ReleaseNoteBlock(
                category_id='improvement',
                target_group_id='user',
                text='release note text in commit 2',
                reference_type=ref_type_commit,
                reference_id='commit-id3',
                user_login=None,
                source_repo=self.cn_current_repo.name(),
                cn_current_repo=self.cn_current_repo),
        ]

        self.assertEqual(expected_rn_objs, actual_rn_objs)

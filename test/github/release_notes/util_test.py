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

import unittest

from github.release_notes.model import (
    Commit,
    REF_TYPE_PULL_REQUEST,
    REF_TYPE_COMMIT
)
from github.release_notes.util import (
    ReleaseNotes,
    pr_number_from_subject,
    commits_from_logs,
    fetch_release_notes_from_commits
)
from github.release_notes.renderer import (
    CATEGORY_ACTION_ID,
    CATEGORY_NOTEWORTHY_ID,
    CATEGORY_IMPROVEMENT_ID,
    TARGET_GROUP_USER_ID,
    TARGET_GROUP_OPERATOR_ID,
    TARGET_GROUP_DEVELOPER_ID,
)
from test.github.release_notes.default_util import (
    release_note_block_with_defaults,
    extract_release_notes_with_defaults,
    CURRENT_REPO
)


class ReleaseNotesTest(unittest.TestCase):

    def test_rls_note_extraction_no_text(self):
        actual_release_notes = extract_release_notes_with_defaults(
            text=None,
        )
        self.assertEqual(0, len(actual_release_notes))

        actual_release_notes = extract_release_notes_with_defaults(
            text='',
        )
        self.assertEqual(0, len(actual_release_notes))

    def test_rls_note_extraction_improvement(self):
        text = \
            '``` improvement user\n'\
            'this is a release note text\n'\
            '```'
        actual_release_notes = extract_release_notes_with_defaults(
            text=text,
        )

        exp_release_note = release_note_block_with_defaults(
            category_id=CATEGORY_IMPROVEMENT_ID,
            target_group_id=TARGET_GROUP_USER_ID,
            text='this is a release note text',
        )
        self.assertEqual([exp_release_note], actual_release_notes)

    def test_rls_note_extraction_ignore_noise_in_header(self):
        def verify_noise_ignored(text):
            actual_release_notes = extract_release_notes_with_defaults(
                text=text,
            )

            exp_release_note = release_note_block_with_defaults(
                text='rlstext',
            )
            self.assertEqual([exp_release_note], actual_release_notes)

        # space before linebreak
        text = \
            '``` improvement user \n'\
            'rlstext\n'\
            '```'
        verify_noise_ignored(text)

        # multiple spaces before linebreak
        text = \
            '``` improvement user     \n'\
            'rlstext\n'\
            '```'
        verify_noise_ignored(text)

        # random text after category and target group
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
        actual_release_notes = extract_release_notes_with_defaults(
            text=text,
        )

        exp_release_note = release_note_block_with_defaults(
            category_id=CATEGORY_NOTEWORTHY_ID,
            target_group_id=TARGET_GROUP_OPERATOR_ID,
            text='notew-text',
        )
        self.assertEqual([exp_release_note], actual_release_notes)

    def test_rls_note_extraction_developer(self):
        text = \
            '``` noteworthy developer\n'\
            'rls-note-for-developer\n'\
            '```'
        actual_release_notes = extract_release_notes_with_defaults(
            text=text,
        )

        exp_release_note = release_note_block_with_defaults(
            category_id=CATEGORY_NOTEWORTHY_ID,
            target_group_id=TARGET_GROUP_DEVELOPER_ID,
            text='rls-note-for-developer',
        )
        self.assertEqual([exp_release_note], actual_release_notes)

    def test_rls_note_extraction_action(self):
        text = \
            '``` action operator\n'\
            'action-text\n'\
            '```'
        actual_release_notes = extract_release_notes_with_defaults(
            text=text,
        )

        exp_release_note = release_note_block_with_defaults(
            category_id=CATEGORY_ACTION_ID,
            target_group_id=TARGET_GROUP_OPERATOR_ID,
            text='action-text',
        )
        self.assertEqual([exp_release_note], actual_release_notes)

    def test_rls_note_extraction_src_repo(self):
        def source_repo_test(
            code_block,
            exp_reference_id,
            exp_usr,
            exp_ref_type=REF_TYPE_PULL_REQUEST
        ):
            actual_release_notes = extract_release_notes_with_defaults(
                text=code_block,
            )
            exp_release_note = release_note_block_with_defaults(
                reference_type=exp_ref_type,
                reference_id=exp_reference_id,
                user_login=exp_usr,
                source_repo='github.com/madeup/source-component',
            )
            self.assertEqual([exp_release_note], actual_release_notes)

        code_block = \
            '``` improvement user github.com/madeup/source-component #1 @source-user-foo\n'\
            'default release note text\n'\
            '```'
        source_repo_test(
            code_block,
            exp_reference_id='1',
            exp_usr='source-user-foo',
        )

        code_block = \
'''``` improvement user github.com/madeup/source-component $commit-id @source-user-foo
default release note text
```'''
        source_repo_test(
            code_block,
            exp_reference_id='commit-id',
            exp_ref_type=REF_TYPE_COMMIT,
            exp_usr='source-user-foo',
        )

        code_block = \
'''``` improvement user github.com/madeup/source-component #1 @source-user-foo some random noise
default release note text
```'''
        source_repo_test(
            code_block,
            exp_reference_id='1',
            exp_usr='source-user-foo',
        )

        code_block = \
'''``` improvement user github.com/madeup/source-component #1 some random noise
default release note text
```'''
        source_repo_test(code_block, exp_reference_id='1', exp_usr=None)

        code_block = \
'''``` improvement user github.com/madeup/source-component @source-user-foo some random noise
default release note text
```'''
        source_repo_test(
            code_block,
            exp_reference_id=None,
            exp_ref_type=None,
            exp_usr='source-user-foo',
        )

        code_block = \
'''``` improvement user github.com/madeup/source-component
default release note text
```'''
        source_repo_test(
            code_block,
            exp_reference_id=None,
            exp_ref_type=None,
            exp_usr=None,
        )

        code_block = \
'''``` improvement user github.com/madeup/source-component some random noise
default release note text
```'''
        source_repo_test(
            code_block,
            exp_reference_id=None,
            exp_ref_type=None,
            exp_usr=None,
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
        actual_release_notes = extract_release_notes_with_defaults(
            text=text,
        )

        exp_release_note1 = release_note_block_with_defaults(
            text='imp-user-text',
        )
        exp_release_note_2 = release_note_block_with_defaults(
            target_group_id=TARGET_GROUP_OPERATOR_ID,
            text='imp-op-text with carriage return and newline feed',
        )
        exp_release_note_3 = release_note_block_with_defaults(
            category_id=CATEGORY_NOTEWORTHY_ID,
            target_group_id=TARGET_GROUP_OPERATOR_ID,
            text='notew-text',
        )
        expected_release_notes = [exp_release_note1, exp_release_note_2, exp_release_note_3]
        self.assertEqual(expected_release_notes, actual_release_notes)

    def test_rls_note_extraction_multiple_lines(self):
        text = \
            '``` improvement user\n'\
            'first line\n'\
            'second line\r\n'\
            'third line\n'\
            '```'
        actual_release_notes = extract_release_notes_with_defaults(
            text=text,
        )

        exp_release_note = release_note_block_with_defaults(
            text='first line\nsecond line\r\nthird line',
        )
        self.assertEqual([exp_release_note], actual_release_notes)

    def test_rls_note_extraction_trim_text(self):
        text = \
            '``` improvement user \n'\
            '\n'\
            '        text with spaces      '\
            '\n'\
            '\n'\
            '```'
        actual_release_notes = extract_release_notes_with_defaults(
            text=text,
        )

        exp_release_note = release_note_block_with_defaults(
            text='text with spaces',
        )
        self.assertEqual([exp_release_note], actual_release_notes)

    def test_rls_note_extraction_no_release_notes(self):
        def verify_no_release_note(text: str):
            actual_release_notes = extract_release_notes_with_defaults(
                text=text,
            )
            self.assertEqual(0, len(actual_release_notes))

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
            rn_block = release_note_block_with_defaults(
                source_repo=None,
            )
            self.fail(
                'a ReleaseNoteBlock always has a source repository, '
                'even if it points to the "current" repository'
            )
        except RuntimeError:
            pass

        rn_block = release_note_block_with_defaults(
            reference_type=None,
            reference_id=None,
            user_login=None,
            source_repo='github.com/madeup/a-foo-bar',
        )
        exp_release_note_block = \
        '``` improvement user github.com/madeup/a-foo-bar\n'\
        'default release note text'\
        '\n```'
        self.assertEqual(exp_release_note_block, rn_block.to_block_str())

        rn_block = release_note_block_with_defaults(
            reference_type=None,
            reference_id=None,
            user_login='a-user',
            source_repo='github.com/madeup/a-foo-bar',
        )
        exp_release_note_block = \
        '``` improvement user github.com/madeup/a-foo-bar @a-user\n'\
        'default release note text'\
        '\n```'
        self.assertEqual(exp_release_note_block, rn_block.to_block_str())

        rn_block = release_note_block_with_defaults(
            reference_type=REF_TYPE_PULL_REQUEST,
            reference_id='123456',
            user_login=None,
            source_repo='github.com/madeup/a-foo-bar',
        )
        exp_release_note_block = \
        '``` improvement user github.com/madeup/a-foo-bar #123456\n'\
        'default release note text'\
        '\n```'
        self.assertEqual(exp_release_note_block, rn_block.to_block_str())

        rn_block = release_note_block_with_defaults(
            reference_type=REF_TYPE_COMMIT,
            reference_id='commit-id',
            user_login='foo',
            source_repo='github.com/madeup/a-foo-bar',
            cn_current_repo=CURRENT_REPO
        )
        exp_release_note_block = \
        '``` improvement user github.com/madeup/a-foo-bar $commit-id @foo\n'\
        'default release note text'\
        '\n```'
        self.assertEqual(exp_release_note_block, rn_block.to_block_str())

        rn_block = release_note_block_with_defaults(
            category_id=CATEGORY_NOTEWORTHY_ID,
            target_group_id=TARGET_GROUP_OPERATOR_ID,
            reference_type=None,
            reference_id=None,
            user_login=None,
            source_repo='github.com/madeup/a-foo-bar',
        )
        exp_release_note_block = \
        '``` noteworthy operator github.com/madeup/a-foo-bar\n'\
        'default release note text'\
        '\n```'
        self.assertEqual(exp_release_note_block, rn_block.to_block_str())

    def test_no_release_note_obj_to_block_str(self):
        rls_note_objs = []
        exp_release_note_block = ''
        self.assertEqual(exp_release_note_block, ReleaseNotes(rls_note_objs).release_note_blocks())

        rls_note_objs = None
        exp_release_note_block = ''
        self.assertEqual(exp_release_note_block, ReleaseNotes(rls_note_objs).release_note_blocks())

    def test_single_release_note_obj_to_block_str(self):
        rls_note_objs = [
            release_note_block_with_defaults()
        ]
        exp_release_note_block = \
        '``` improvement user github.com/madeup/current-repo #42 @foo\n'\
        'default release note text'\
        '\n```'
        self.assertEqual(exp_release_note_block, ReleaseNotes(rls_note_objs).release_note_blocks())

    def test_multiple_release_note_objs_to_block_str(self):
        rls_note_objs = [
            release_note_block_with_defaults(),
            release_note_block_with_defaults(
                reference_type=REF_TYPE_COMMIT,
                reference_id='commit-id',
                text='another one',
                source_repo='github.com/s/repo',
            ),
        ]
        exp_release_note_block = \
        '``` improvement user github.com/madeup/current-repo #42 @foo\n'\
        'default release note text'\
        '\n```'\
        '\n'\
        '\n'\
        '``` improvement user github.com/s/repo $commit-id @foo\n'\
        'another one'\
        '\n```'
        self.assertEqual(exp_release_note_block, ReleaseNotes(rls_note_objs).release_note_blocks())

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
        actual_rls_note_objs = fetch_release_notes_from_commits(commits, CURRENT_REPO)
        expected_rls_note_objs = [
            release_note_block_with_defaults(
                category_id=CATEGORY_IMPROVEMENT_ID,
                target_group_id=TARGET_GROUP_USER_ID,
                text='release note text in commit',
                reference_type=REF_TYPE_COMMIT,
                reference_id='commit-id2',
                user_login=None,
                source_repo=CURRENT_REPO.name(),
                cn_current_repo=CURRENT_REPO),
            release_note_block_with_defaults(
                category_id=CATEGORY_IMPROVEMENT_ID,
                target_group_id=TARGET_GROUP_USER_ID,
                text='release note text in commit 2',
                reference_type=REF_TYPE_COMMIT,
                reference_id='commit-id3',
                user_login=None,
                source_repo=CURRENT_REPO.name(),
                cn_current_repo=CURRENT_REPO),
        ]

        self.assertEqual(expected_rls_note_objs, actual_rls_note_objs)

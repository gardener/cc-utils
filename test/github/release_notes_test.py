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
    ReleaseNote,
    build_markdown
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
            user_login='foo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNote(
                category_id='improvement',
                target_group_id='user',
                text='this is a release note text',
                pr_number=42,
                user_login='foo'),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_noteworthy(self):
        text = \
            '``` noteworthy operator\n'\
            'notew-text\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNote(
                category_id='noteworthy',
                target_group_id='operator',
                text='notew-text',
                pr_number=42,
                user_login='foo'),
            _.nth(release_notes, 0)
        )

    def test_multiple_rls_note_extraction(self):
        text = \
            'random text\n'\
            '``` improvement user\n'\
            'imp-user-text\n'\
            '```\n'\
            '``` improvement operator\n'\
            'imp-op-text\n'\
            '```\n'\
            '``` noteworthy operator\n'\
            'notew-text\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo'
        )

        self.assertEqual(3, len(release_notes))
        self.assertEqual(
            ReleaseNote(
                category_id='improvement',
                target_group_id='user',
                text='imp-user-text',
                pr_number=42,
                user_login='foo'),
            _.nth(release_notes, 0)
        )
        self.assertEqual(
            ReleaseNote(
                category_id='improvement',
                target_group_id='operator',
                text='imp-op-text',
                pr_number=42,
                user_login='foo'),
            _.nth(release_notes, 1)
        )
        self.assertEqual(
            ReleaseNote(
                category_id='noteworthy',
                target_group_id='operator',
                text='notew-text',
                pr_number=42,
                user_login='foo'),
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
            user_login='foo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNote(
                category_id='improvement',
                target_group_id='user',
                text='first line\nsecond line\r\nthird line',
                pr_number=42,
                user_login='foo'),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_trim_text(self):
        text = \
            '``` improvement user\n'\
            '\n'\
            '        text with spaces      '\
            '\n'\
            '\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNote(
                category_id='improvement',
                target_group_id='user',
                text='text with spaces',
                pr_number=42,
                user_login='foo'),
            _.nth(release_notes, 0)
        )

    def test_rls_note_extraction_no_target_group_should_default_to_user(self):
        text = \
            '``` improvement\n'\
            'text\n'\
            '```'
        release_notes = extract_release_notes(
            pr_number=42,
            text=text,
            user_login='foo'
        )

        self.assertEqual(1, len(release_notes))
        self.assertEqual(
            ReleaseNote(
                category_id='improvement',
                target_group_id='user',
                text='text',
                pr_number=42,
                user_login='foo'),
            _.nth(release_notes, 0)
        )

    def test_build_markdown(self):
        release_note_objs = [
            ReleaseNote(
              category_id='improvement',
              target_group_id='user',
              text='rls note 1',
              pr_number=42,
              user_login='foo'
            ),
            ReleaseNote(
                category_id='improvement',
                target_group_id='user',
                text='rls note 2',
                pr_number=42,
                user_login='foo'
            )
        ]
        actual_str = build_markdown(release_note_objs)

        expected_str = \
            '## Improvements\n'\
            '### To end users\n'\
            '* rls note 1 (#42, @foo)\n'\
            '* rls note 2 (#42, @foo)'
        self.assertEquals(expected_str, actual_str)

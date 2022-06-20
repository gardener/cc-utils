# Copyright (c) 2022 SAP SE or an SAP affiliate company. All rights reserved. This file is
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

import cosign.payload as cp


class PayloadTest(unittest.TestCase):
    def test_json_marshaling_with_annotations(self):
        expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
            '"image":{"docker-manifest-digest":' \
            '"sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b"},' \
            '"type":"cosign container image signature"},"optional":{"key":"val"}}'

        img_ref = 'eu.gcr.io/test/img@' \
            'sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b'
        annotations = {
            "key": "val",
        }

        payload = cp.Payload(
            image_ref=img_ref,
            annotations=annotations,
        )

        actual_json = payload.json()

        self.assertEqual(actual_json, expected_json)

    def test_json_marshaling_without_annotations(self):
        expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
            '"image":{"docker-manifest-digest":' \
            '"sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b"},' \
            '"type":"cosign container image signature"},"optional":null}'

        img_ref = 'eu.gcr.io/test/img@' \
            'sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b'

        payload = cp.Payload(
            image_ref=img_ref,
        )

        actual_json = payload.json()

        self.assertEqual(actual_json, expected_json)

    def test_raise_error_for_img_ref_without_digest(self):
        img_ref = 'eu.gcr.io/test/img:1.0.0'
        self.assertRaises(ValueError, cp.Payload, self, img_ref)

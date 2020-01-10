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

import container.registry as examinee


class RegistryTest(unittest.TestCase):
    def test_normalise_image_reference(self):
        # do not change fully qualified reference
        reference = 'foo.io/my/image:1.2.3'
        self.assertEqual(
            examinee.normalise_image_reference(reference),
            reference,
        )

        # prepend default registry (docker.io) if no host given
        reference = 'my/image:1.2.3'
        self.assertEqual(
            examinee.normalise_image_reference(reference),
            'registry-1.docker.io/' + reference,
        )

        # insert 'library' if no "owner" is given
        reference = 'alpine:1.2.3'
        self.assertEqual(
            examinee.normalise_image_reference(reference),
            'registry-1.docker.io/library/' + reference,
        )

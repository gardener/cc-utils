# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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

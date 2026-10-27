# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import signingserver


def test_distinguished_name():
    assert signingserver.DistinguishedName.parse('my-name') == signingserver.DistinguishedName(
        common_name='my-name',
    )

    assert signingserver.DistinguishedName.parse('O=my-org') == signingserver.DistinguishedName(
        organization='my-org',
    )

    assert signingserver.DistinguishedName.parse('O=my-org+OU=my-org-unit') \
        == signingserver.DistinguishedName(
            organization='my-org',
            organizational_unit='my-org-unit',
        )

    with pytest.raises(ValueError):
        signingserver.DistinguishedName.parse('   ')

    with pytest.raises(ValueError):
        signingserver.DistinguishedName.parse('Y=wrong-attribute')


def test_signingserver_hash_algorithm_mapping():
    # hashlib-style names map to the server's non-deprecated (dashed) form
    assert signingserver._signingserver_hash_algorithm['sha256'] == 'SHA-256'
    assert signingserver._signingserver_hash_algorithm['sha512'] == 'SHA-512'

    # The mapping is applied as it is in `sign()` when building the wire param
    def wire_name(hash_algorithm):
        return signingserver._signingserver_hash_algorithm.get(hash_algorithm, hash_algorithm)

    assert wire_name('sha256') == 'SHA-256'
    # Already-dashed / unknown names are forwarded unchanged
    assert wire_name('SHA-256') == 'SHA-256'
    assert wire_name('') == ''

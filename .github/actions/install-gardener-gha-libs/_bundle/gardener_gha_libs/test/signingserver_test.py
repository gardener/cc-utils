# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
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

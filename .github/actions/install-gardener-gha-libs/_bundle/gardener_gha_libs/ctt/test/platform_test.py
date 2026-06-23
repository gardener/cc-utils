# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import oci.model as om

import ctt.oci_platform as platform


def test_platform_filter():
    filter_func = platform.PlatformFilter(
        included_platform_regexes=(
            'linux/.*',
        ),
    )

    matches = filter_func(om.OciPlatform(
        os='linux',
        architecture='amd64',
    ))
    assert matches is True

    matches = filter_func(om.OciPlatform(
        os='linux',
        architecture='arm64',
        variant='v6',
    ))
    assert matches is True

    matches = filter_func(om.OciPlatform(
        os='darwin',
        architecture='arm64',
    ))
    assert matches is False

    filter_func = platform.PlatformFilter(
        included_platform_regexes=(
            'linux/arm64',
        ),
    )

    matches = filter_func(om.OciPlatform(
        os='linux',
        architecture='arm64',
    ))
    assert matches is True

    matches = filter_func(om.OciPlatform(
        os='linux',
        architecture='arm64',
        variant='v7',
    ))
    assert matches is False

    matches = filter_func(om.OciPlatform(
        os='linux',
        architecture='amd64',
    ))
    assert matches is False

    filter_func = platform.PlatformFilter(
        included_platform_regexes=(
            'linux/arm64/v7',
        ),
    )

    matches = filter_func(om.OciPlatform(
        os='linux',
        architecture='arm64',
        variant='v7',
    ))
    assert matches is True

    matches = filter_func(om.OciPlatform(
        os='linux',
        architecture='arm64',
        variant='v6',
    ))
    assert matches is False

    filter_func = platform.PlatformFilter(
        included_platform_regexes=(
            '.*/arm64/.*',
        ),
    )

    matches = filter_func(om.OciPlatform(
        os='ios',
        architecture='arm64',
        variant='v7',
    ))
    assert matches is True

    matches = filter_func(om.OciPlatform(
        os='ios',
        architecture='arm',
        variant='v7',
    ))
    assert matches is False

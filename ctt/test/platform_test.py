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

import oci.model as om

import ctt.platform as platform


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

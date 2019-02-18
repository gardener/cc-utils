# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

import re

import product.model


def image_reference_filter(include_regexes=(), exclude_regexes=()):
    # compile regexes
    include_functions = [re.compile(r).fullmatch for r in include_regexes]
    exclude_functions = [re.compile(r).fullmatch for r in exclude_regexes]

    def _img_ref_filter(image_reference: product.model.ContainerImage):
        matches = True
        if include_functions:
            matches &= any(
                map(lambda f: f(image_reference.image_reference()), include_functions)
            )

        # exclusion filter has precedence
        if exclude_functions:
            matches &= not any(
                map(lambda f: f(image_reference.image_reference()), exclude_functions)
            )

        return matches

    return _img_ref_filter

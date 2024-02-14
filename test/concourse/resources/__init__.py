# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import sys
import os

# add modules from root dir to module search path
# so unit test modules can use regular imports
sys.path.extend(
    (
        os.path.join(
            os.path.realpath(os.path.dirname(__file__)),
            os.pardir,
            os.pardir,
            os.pardir,
            os.pardir,
        ),
        os.path.realpath(os.path.dirname(__file__))
    )
)


def mako_resource_dir():
    return os.path.join(
        os.path.abspath(
            os.path.join(
                os.path.realpath(os.path.dirname(__file__)),
                os.pardir,
                os.pardir,
                os.pardir,
                'concourse',
                'resources',
            )
        )
    )

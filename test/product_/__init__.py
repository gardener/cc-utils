# SPDX-FileCopyrightText: Copyright Contributors to the Gardener project
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
            os.pardir
        ),
        os.path.realpath(os.path.dirname(__file__))
    )
)

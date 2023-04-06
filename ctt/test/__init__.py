# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import sys
import os

own_dir = os.path.abspath(os.path.dirname(__name__))
repo_root = os.path.abspath(os.path.join(own_dir, os.path.pardir))

sys.path.insert(1, repo_root)

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

import os
import subprocess
import sys

# assumption: we reside exactly one directory below our sources
src_dir = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        os.pardir
    )
)
cli_py = os.path.join(src_dir, 'cli_gen.py')

repo_root = os.path.abspath(
    os.path.join(
        src_dir,
        os.pardir,
        os.pardir,
    )
)
# hacky: add cc-utils (gardener-cicd-libs) to PYTHONPATH, so it's available during
# test-execution
sys.path.insert(1, repo_root)


def test_smoke():
    # perform a very weak smoke-test:
    # test if a trivial sub-command can be run
    result = subprocess.run(
        [cli_py, 'config', '-h'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )

    assert result.returncode == 0
    assert result.stderr.strip() == ''
    assert result.stdout.strip().startswith('usage: cli.py config')

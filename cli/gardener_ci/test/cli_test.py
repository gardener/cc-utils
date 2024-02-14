# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
        [cli_py, '-h'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )

    assert result.returncode == 0
    assert result.stderr.strip() == ''
    assert result.stdout.strip().startswith('usage: cli_gen.py')

# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import select
import sys

import util
from util import CliHint
import version

def process_version(
    input_file: CliHint(help="path to read input semver from")=None,
    version_string: CliHint(help="semver string to use as input")=None,
    output_file: CliHint(typehint=[str], help="file path(s) to write effective version to")=None,
    operation: CliHint(help="version transformation", choices=version.ALL_OPS)=None,
    prerelease: CliHint(help="value to set prerelease to")=None,
    build_metadata: CliHint(help="value to set build-metadata to")=None,
    build_metadata_length: CliHint(help="cut-off build-metadata after specified # of chars")=12,
    verbatim_version: CliHint(help="value to set the version to")=None
    ):
    # validate arguments
    if input_file and version_string:
        util.fail("Only one of '--input-file' and '--version-string' may be specified.")
    if input_file:
        util.existing_file(input_file)
    if output_file:
        if output_file != sys.stdout:
            if type(output_file) != list:
                output_file = [output_file]
        for file_name in output_file:
            pardir = os.path.dirname(os.path.abspath(file_name))
            if not os.path.isdir(pardir):
                util.fail('{f} must reside in an existing directory.'.format(f=file_name))

    # retrieve input
    if not input_file:
        if version_string:
            version_str = version_string
        else:
            # We don't want blocking reads from stdin. Since the chosen approach does not work
            # on Windows, however, we accept the blocking read in that case.
            if os.name == "nt":
                version_str = sys.stdin.read()
            elif sys.stdin in select.select([sys.stdin],[],[],0):
                version_str = sys.stdin.read()
            else:
                util.fail("No input ready on stdin.")
    else:
        with open(input_file) as f:
            version_str = f.read().strip()

    try:
        processed_version = version.process_version(
            version_str=version_str,
            operation=operation,
            prerelease=prerelease,
            build_metadata=build_metadata,
            build_metadata_length=build_metadata_length,
            verbatim_version=verbatim_version,
        )
    except ValueError as ve:
        util.fail(str(ve))

    if not output_file:
        outf = [sys.stdout]
    else:
        outf = map(lambda f: open(f, 'w'), output_file)
        # if we do not write to stdout, output the effective version there as well
        util.info("effective version: " + processed_version)

    for fh in outf:
        try:
            fh.write(processed_version)
        finally:
            if fh != sys.stdout:
                fh.close()

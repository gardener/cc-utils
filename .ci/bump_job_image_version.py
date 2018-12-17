#!/usr/bin/env python3

import os
import pathlib
import fileinput

from util import (
    check_env,
    existing_file,
)

repo_dir = check_env('REPO_DIR')
effective_version = check_env('EFFECTIVE_VERSION')

template_file = existing_file(pathlib.Path(repo_dir, 'concourse', 'resources', 'defaults.mako'))

lines_replaced = 0

for line in fileinput.FileInput(str(template_file), inplace=True):
    if 'tag:' in line:
        if lines_replaced is not 0:
            raise RuntimeError(f'More than one image tag found in template file')
        leading_spaces = line.index('tag:')
        print(f'{leading_spaces * " "}tag: "{effective_version}"')
        lines_replaced = 1
    else:
        print(line, end='')

#!/usr/bin/env python3

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
string_to_match = 'tag = '

for line in fileinput.FileInput(str(template_file), inplace=True):
    if string_to_match in line:
        if lines_replaced != 0:
            raise RuntimeError(f'More than one image tag found in template file')
        leading_spaces = line.index(string_to_match)
        print(f'{leading_spaces * " "}{string_to_match}"{effective_version}"')
        lines_replaced = 1
    else:
        print(line, end='')

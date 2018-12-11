#!/usr/bin/env python3

import os
import pathlib
import fileinput

repo_dir = os.environ.get('REPO_DIR')
if repo_dir is None:
    raise RuntimeError('REPO_DIR environment variable must be set')

version_path = os.environ.get('VERSION_PATH')
if version_path is None:
    raise RuntimeError('VERSION_PATH environment variable must be set')

template_file = pathlib.Path(repo_dir, 'concourse', 'resources', 'defaults.mako')
version_file = pathlib.Path(version_path, 'version')

if not template_file.is_file():
    raise RuntimeError(f'Template file not found at {template_file}')
if not version_file.is_file():
    raise RuntimeError(f'Verson file not found at {version_file}')

version = version_file.read_text()
lines_replaced = 0

for line in fileinput.FileInput(str(template_file), inplace=True):
    if 'tag:' in line:
        if lines_replaced is not 0:
            raise RuntimeError(f'More than one image tag found in template file')
        leading_spaces = line.index('tag:')
        print(f'{leading_spaces * " "}tag: "{version}"')
        lines_replaced = 1
    else:
        print(line, end='')

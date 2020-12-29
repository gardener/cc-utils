#!/usr/bin/env python3

import concourse.paths
from ci.util import (
    check_env,
    existing_file,
)

repo_dir = check_env('REPO_DIR')
effective_version = check_env('EFFECTIVE_VERSION')

last_tag_file = existing_file(concourse.paths.last_released_tag_file)

with open(last_tag_file, 'w') as f:
    f.write(effective_version)

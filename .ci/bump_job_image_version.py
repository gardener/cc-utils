#!/usr/bin/env python3

import os

from ci.util import (
    check_env,
    existing_file,
)

repo_dir = check_env('REPO_DIR')
effective_version = check_env('EFFECTIVE_VERSION')

last_tag_file = existing_file(os.path.join(repo_dir, 'concourse', 'resources', 'LAST_RELEASED_TAG'))

with open(last_tag_file, 'w') as f:
    f.write(effective_version)

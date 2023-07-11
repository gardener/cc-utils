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

import logging

import ccc.github
import ci.log
import gci.componentmodel

from github.util import GitHubRepositoryHelper

from ci.util import ctx
from gitutil import GitHelper
from slackclient.util import SlackHelper


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


def post_to_slack(
    release_notes_markdown: str,
    github_repository_name: str,
    slack_cfg_name: str,
    slack_channel: str,
    release_version: str,
    max_msg_size_bytes: int=20000,
):
    # XXX slack imposes a maximum msg size
    # https://api.slack.com/changelog/2018-04-truncating-really-long-messages#

    slack_cfg = ctx().cfg_factory().slack(slack_cfg_name)
    slack_helper = SlackHelper(slack_cfg)

    idx = 0
    i = 0

    try:
        while True:
            title = f'[{github_repository_name}:{release_version} released'

            # abort on last
            if idx + max_msg_size_bytes > len(release_notes_markdown):
                did_split = i > 0
                if did_split:
                    title += ' - final]'
                else:
                    title += ']'

                msg = release_notes_markdown[idx:]
                yield slack_helper.post_to_slack(channel=slack_channel, title=title, message=msg)
                break

            # post part
            title += f' - part {i} ]'
            msg = release_notes_markdown[idx: idx+max_msg_size_bytes]
            logger.info(f"Posting release-note '{title}'")
            yield slack_helper.post_to_slack(channel=slack_channel, title=title, message=msg)

            i += 1
            idx += max_msg_size_bytes

    except RuntimeError as e:
        logger.warning(e)


def delete_file_from_slack(
    slack_cfg_name: str,
    file_id: str,
):
    slack_cfg = ctx().cfg_factory().slack(slack_cfg_name)
    response = SlackHelper(slack_cfg).delete_file(
        file_id=file_id,
    )
    return response


def draft_release_name_for_version(release_version: str):
    return "{v}-draft".format(v=release_version)


def github_helper_from_github_access(
    github_access=gci.componentmodel.GithubAccess,
):
    logger.info(f'Creating GH Repo-helper for {github_access.repoUrl}')
    return GitHubRepositoryHelper(
        github_api=ccc.github.github_api_from_gh_access(github_access),
        owner=github_access.org_name(),
        name=github_access.repository_name(),
    )


def git_helper_from_github_access(
    github_access: gci.componentmodel.GithubAccess,
    repo_path: str,
):
    logger.info(f'Creating Git-helper for {github_access.repoUrl}')
    return GitHelper(
        repo=repo_path,
        github_cfg=ccc.github.github_cfg_for_repo_url(github_access.repoUrl),
        github_repo_path=f'{github_access.org_name()}/{github_access.repository_name()}',
    )

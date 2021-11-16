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

import slack

import ci.log
import model.slack


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


class SlackHelper:
    def __init__(
            self,
            slack_cfg: model.slack.SlackConfig
    ):
        self.slack_cfg = slack_cfg

    def post_to_slack(
        self,
        channel: str,
        title: str,
        message: str,
        filetype: str='post'
    ):
        api_token = self.slack_cfg.api_token()

        if not api_token:
            raise RuntimeError("can't post to slack as there is no slack api token in config")

        logger.info(f"posting message '{title}' to slack channel '{channel}'")
        client = slack.WebClient(token=api_token)
        # We expect rather long messages, so we do not use incoming webhooks etc. to post
        # messages as those get truncated, see
        # https://api.slack.com/changelog/2018-04-truncating-really-long-messages
        # Instead we use the file upload mechanism so that this limit does not apply.
        # For contents of result see https://api.slack.com/methods/files.upload
        response = self._post_with_retry(
            client=client,
            retries=5,
            channels=channel,
            content=message,
            title=title,
            filetype=filetype,
        )
        if not response['ok']:
            raise RuntimeError(f"failed to post to slack channel '{channel}': {response['error']}")
        return response

    def _post_with_retry(self, client, retries=5, **kwargs):
        try:
            response = client.files_upload(**kwargs)
            return response
        except slack.errors.SlackApiError as sae:
            error_code = sae.response.get('error')
            if retries < 1:
                raise sae # no retries left (or none requested)
            if error_code == 'markdown_conversion_failed_because_of_read_failed':
                logger.warning(f'received {error_code} - retrying {retries}')
                return self._post_with_retry(client=client, retries=retries-1, **kwargs)
            else:
                raise sae # only retry for known sporadic err

    def delete_file(
        self,
        file_id: str,
    ):
        api_token = self.slack_cfg.api_token()
        if not api_token:
            raise RuntimeError("can't post to slack as there is no slack api token in config")
        logger.info(f"deleting file with id '{file_id}' from Slack")
        client = slack.WebClient(token=api_token)
        response = client.files_delete(
            id=file_id,
        )
        if not response['ok']:
            raise RuntimeError(f"failed to delete file with id {file_id}")
        return response

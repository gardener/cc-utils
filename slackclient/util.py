# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import slack

from ci.util import info, warning
from model.slack import SlackConfig


class SlackHelper(object):
    def __init__(
            self,
            slack_cfg: SlackConfig
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

        info(f"posting message '{title}' to slack channel '{channel}'")
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
                warning(f'received {error_code} - retrying {retries}')
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
        info(f"deleting file with id '{file_id}' from Slack")
        client = slack.WebClient(token=api_token)
        response = client.files_delete(
            id=file_id,
        )
        if not response['ok']:
            raise RuntimeError(f"failed to delete file with id {file_id}")
        return response

# SPDX-FileCopyrightText: 2020 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import slack

import model.slack


def client(slack_cfg: model.slack.SlackConfig):
    return slack.WebClient(token=slack_cfg.api_token())

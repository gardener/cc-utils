# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import jira

import model.jira


def from_cfg(
    jira_cfg:model.jira.JiraConfig
):
    raise NotImplementedError()


def _from_cfg(
    jira_cfg:model.jira.JiraConfig
) -> jira.JIRA:
    credentials = jira_cfg.credentials()
    return jira.JIRA(
        server=jira_cfg.base_url(),
        basic_auth=credentials.as_tuple(),
    )

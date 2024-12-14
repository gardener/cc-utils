# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import typing

import github3

RepoUrl: typing.TypeAlias = str
GithubApiLookup = typing.Callable[[RepoUrl], github3.GitHub]

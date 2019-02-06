<%def
  name="draft_release_step(job_step, job_variant, github_cfg, indent)",
  filter="indent_func(indent),trim">
<%
from makoutil import indent_func
import os
version_file = job_step.input('version_path') + '/version'
repo = job_variant.main_repository()
draft_release_trait = job_variant.trait('draft_release')
version_operation = draft_release_trait._preprocess()
%>
import version
import pathlib

import util

from gitutil import GitHelper
from github.release_notes.util import (
    draft_release_name_for_version,
    ReleaseNotes,
    github_repo_path,
)
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)

if '${version_operation}' != 'finalize':
    raise NotImplementedError(
        "Version-processing other than 'finalize' is not supported for draft release creation"
    )

version_file = util.existing_file(pathlib.Path('${version_file}'))
processed_version = version.process_version(
    version_str=version_file.read_text().strip(),
    operation='${version_operation}',
)

github_cfg = util.ctx().cfg_factory().github('${github_cfg.name()}')

githubrepobranch = GitHubRepoBranch(
    github_config=github_cfg,
    repo_owner='${repo.repo_owner()}',
    repo_name='${repo.repo_name()}',
    branch='${repo.branch()}',
)

helper = GitHubRepositoryHelper.from_githubrepobranch(
    githubrepobranch=githubrepobranch,
)

git_helper = GitHelper.from_githubrepobranch(
        githubrepobranch=githubrepobranch,
        repo_path='${repo.resource_name()}',
    )

release_notes_md = ReleaseNotes.create(
    github_helper=helper,
    git_helper=git_helper,
    repository_branch='${repo.branch()}'
).to_markdown()

draft_name = draft_release_name_for_version(processed_version)
draft_release = helper.draft_release_with_name(draft_name)
if not draft_release:
    helper.create_release(
        tag_name='',
        name=draft_name,
        body=release_notes_md,
        draft=True,
        prerelease=False
    )
else:
    if not draft_release.body == release_notes_md:
        draft_release.edit(body=release_notes_md)
    else:
        util.info('draft release notes are already up to date')

</%def>

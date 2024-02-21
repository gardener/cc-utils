<%def
  name="draft_release_step(job_step, job_variant, github_cfg, indent)",
  filter="indent_func(indent),trim">
<%
from makoutil import indent_func
import ci.util
import os
import concourse.steps.component_descriptor_util as cdu
import gci.componentmodel
version_file = job_step.input('version_path') + '/version'
repo = job_variant.main_repository()
draft_release_trait = job_variant.trait('draft_release')
component_descriptor_trait = job_variant.trait('component_descriptor')
component_name = component_descriptor_trait.component_name()
version_operation = draft_release_trait._preprocess()
component_descriptor_path = os.path.join(
    job_step.input('component_descriptor_dir'),
    cdu.component_descriptor_fname(gci.componentmodel.SchemaVersion.V2),
)
%>
import logging
import os
import version

import ccc.github
import ci.log
import ci.util
import cnudie.retrieve
import cnudie.util
import gci.componentmodel as cm
import release_notes.fetch
import release_notes.markdown

from gitutil import GitHelper
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)

logger = logging.getLogger('draft-release')
ci.log.configure_default_logging()

if '${version_operation}' != 'finalize':
    raise NotImplementedError(
        "Version-processing other than 'finalize' is not supported for draft release creation"
    )

with open('${version_file}') as f:
  version_str = f.read().strip()

processed_version = version.process_version(
    version_str=version_str,
    operation='${version_operation}',
)

repo_dir = ci.util.existing_dir('${repo.resource_name()}')

have_cd = os.path.exists(component_descriptor_path := '${component_descriptor_path}')

if have_cd:
    component = cm.ComponentDescriptor.from_dict(
            component_descriptor_dict=ci.util.parse_yaml_file(
                component_descriptor_path,
            ),
            validation_mode=cm.ValidationMode.WARN,
    ).component
else:
   print('did not find component-descriptor')
   exit(1)

github_cfg = ccc.github.github_cfg_for_repo_url(
  ci.util.urljoin(
    '${repo.repo_hostname()}',
    '${repo.repo_path()}'
  ),
)

<%
import concourse.steps
template = concourse.steps.step_template('component_descriptor')
ocm_repository_lookup = template.get_def('ocm_repository_lookup').render
%>
${ocm_repository_lookup(component_descriptor_trait.ocm_repository_mappings())}

component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
    ocm_repository_lookup=ocm_repository_lookup,
)
ocm_version_lookup = cnudie.retrieve.version_lookup(
    ocm_repository_lookup=ocm_repository_lookup,
)

githubrepobranch = GitHubRepoBranch(
    github_config=github_cfg,
    repo_owner='${repo.repo_owner()}',
    repo_name='${repo.repo_name()}',
    branch='${repo.branch()}',
)

github_helper = GitHubRepositoryHelper.from_githubrepobranch(
    githubrepobranch=githubrepobranch,
)
try:
    release_note_blocks = release_notes.fetch.fetch_draft_release_notes(
        repo_path=repo_dir,
        component=component,
        component_descriptor_lookup=component_descriptor_lookup,
        version_lookup=ocm_version_lookup,
        current_version=version_str,
    )
    release_notes_md = '\n'.join(
        str(i) for i in release_notes.markdown.render(release_note_blocks)
    ) or 'no release notes available'
except ValueError as e:
    ci.util.warning(f'Error when computing release notes: {e}')
    # this will happen if a component-descriptor for a more recent version than what is available in the
    # repository is already published - usually by steps that erroneously publish them before they should.
    release_notes_md = 'no release notes available'

draft_name = f'{processed_version}-draft'
draft_release = github_helper.draft_release_with_name(draft_name)
if not draft_release:
    ci.util.info(f"Creating draft-release '{draft_name}'")
    github_helper.create_draft_release(
        name=draft_name,
        body=release_notes_md,
        component_version=processed_version,
        component_name=component.name,
    )
else:
    if not draft_release.body == release_notes_md:
        ci.util.info(f"Updating draft-release '{draft_name}'")
        draft_release.edit(body=release_notes_md)
    else:
        ci.util.info('draft release notes are already up to date')

ci.util.info("Checking for outdated draft releases to delete")
for release, deletion_successful in github_helper.delete_outdated_draft_releases():
    if deletion_successful:
        ci.util.info(f"Deleted release '{release.name}'")
    else:
        ci.util.warning(f"Could not delete release '{release.name}'")
</%def>

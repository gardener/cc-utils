<%def
  name="component_descriptor_step(job_step, job_variant, output_image_descriptors, indent)",
  filter="indent_func(indent),trim"
>
<%
import collections
import dataclasses
from makoutil import indent_func
from concourse.steps import step_lib
import gci.componentmodel as cm
import concourse.model.traits.component_descriptor as comp_descr_trait

descriptor_trait = job_variant.trait('component_descriptor')
main_repo = job_variant.main_repository()
main_repo_labels = main_repo.source_labels()
main_repo_path_env_var = main_repo.logical_name().replace('-', '_').upper() + '_PATH'
other_repos = [r for r in job_variant.repositories() if not r.is_main_repo()]
ctx_repository_base_url = descriptor_trait.ctx_repository_base_url()
retention_policy = descriptor_trait.retention_policy()
ocm_repository_mappings = descriptor_trait.ocm_repository_mappings()

snapshot_ctx_repository_base_url = descriptor_trait.snapshot_ctx_repository_base_url() or ""

# label main repo as main
if not 'cloud.gardener/cicd/source' in [label.name for label in main_repo_labels]:
  main_repo_labels.append(
    cm.Label(
      name='cloud.gardener/cicd/source',
      value={'repository-classification': 'main'},
    )
  )

# group images by _base_name
images_by_base_name = collections.defaultdict(list)
for image_descriptor in output_image_descriptors.values():
  images_by_base_name[image_descriptor._base_name].append(image_descriptor)
%>
import dataclasses
import enum
import json
import logging
import os
import pprint
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback

import dacite
import git
import yaml

import ccc.oci
import cnudie.purge
import cnudie.retrieve
import cnudie.upload
import cnudie.util
import gci.componentmodel as cm
import oci.auth as oa
import version
# required for deserializing labels
Label = cm.Label

from ci.util import fail, parse_yaml_file, ctx

logger = logging.getLogger('step.component_descriptor')

${step_lib('component_descriptor')}
${step_lib('component_descriptor_util')}

snapshot_ctx_repository_base_url = "${snapshot_ctx_repository_base_url}"

# retrieve effective version
version_file_path = os.path.join(
  '${job_step.input('version_path')}',
  'version',
)
with open(version_file_path) as f:
  effective_version = f.read().strip()

component_name = '${descriptor_trait.component_name()}'
component_labels = ${descriptor_trait.component_labels()}
component_name_v2 = component_name.lower() # OCI demands lowercase
ctx_repository_base_url = '${descriptor_trait.ctx_repository_base_url()}'

mapping_config = cnudie.util.OcmLookupMappingConfig.from_dict(
    raw_mappings = ${ocm_repository_mappings},
)

main_repo_path = os.path.abspath('${main_repo.resource_name()}')
commit_hash = head_commit_hexsha(main_repo_path)

main_repo_url = '${main_repo.repo_hostname()}/${main_repo.repo_path()}'

# create base descriptor filled with default values
base_descriptor_v2 = base_component_descriptor_v2(
    component_name_v2=component_name_v2,
    component_labels=component_labels,
    effective_version=effective_version,
    source_labels=${[dataclasses.asdict(label) for label in main_repo_labels]},
    ctx_repository_base_url=ctx_repository_base_url,
    commit=commit_hash,
    repo_url=main_repo_url,
)
component_v2 = base_descriptor_v2.component

## XXX unify w/ injection-method used for main-repository
% for repository in other_repos:
repo_labels = ${repository.source_labels()}
if not 'cloud.gardener/cicd/source' in [label.name for label in repo_labels]:
  repo_labels.append(
    cm.Label(
      name='cloud.gardener/cicd/source',
      value={'repository-classification': 'auxiliary'},
    ),
  )

if not (repo_commit_hash := head_commit_hexsha(os.path.abspath('${repository.resource_name()}'))):
    logger.warning('Could not determine commit hash')

component_v2.sources.append(
    cm.ComponentSource(
        name='${repository.logical_name().replace('/', '_').replace('.', '_')}',
        type=cm.SourceType.GIT,
        access=cm.GithubAccess(
            type=cm.AccessType.GITHUB,
            repoUrl='${repository.repo_hostname()}/${repository.repo_path()}',
            ref='${repository.branch()}',
            commit=repo_commit_hash,
        ),
        version=effective_version,
        labels=repo_labels,
    )
)
% endfor

# add own container image references
% for name, image_descriptors in images_by_base_name.items():
component_v2.resources.append(
  cm.Resource(
    name='${name}',
    version=effective_version, # always inherited from component
    type=cm.ResourceType.OCI_IMAGE,
    relation=cm.ResourceRelation.LOCAL,
    access=cm.OciAccess(
      type=cm.AccessType.OCI_REGISTRY,
      imageReference='${image_descriptors[0].image_reference()}' + ':' + effective_version,
    ),
    labels=${image_descriptors[0].resource_labels()}
  ),
)
% endfor

logger.info('default component descriptor:\n')
print(dump_component_descriptor_v2(base_descriptor_v2))
print('\n' * 2)

descriptor_out_dir = os.path.abspath('${job_step.output("component_descriptor_dir")}')

v2_outfile = os.path.join(
  descriptor_out_dir,
  component_descriptor_fname(schema_version=gci.componentmodel.SchemaVersion.V2),
)

descriptor_script = os.path.abspath(
  '${job_variant.main_repository().resource_name()}/.ci/component_descriptor'
)
if os.path.isfile(descriptor_script):
  is_executable = bool(os.stat(descriptor_script)[stat.ST_MODE] & stat.S_IEXEC)
  if not is_executable:
    fail(f'descriptor script file exists but is not executable: {descriptor_script}')

  # dump base_descriptor_v2 and pass it to descriptor script
  base_component_descriptor_fname = (
    f'base_{component_descriptor_fname(schema_version=gci.componentmodel.SchemaVersion.V2)}'
  )
  base_descriptor_file_v2 = os.path.join(
    descriptor_out_dir,
    base_component_descriptor_fname,
  )
  with open(base_descriptor_file_v2, 'w') as f:
    f.write(dump_component_descriptor_v2(base_descriptor_v2))

  ocm_config_file = os.path.join(descriptor_out_dir, 'ocm-cfg.yaml')
  with open(ocm_config_file, 'w') as f:
    f.write(mapping_config.to_ocm_software_config())

  subproc_env = os.environ.copy()
  subproc_env['${main_repo_path_env_var}'] = main_repo_path
  subproc_env['MAIN_REPO_DIR'] = main_repo_path
  subproc_env['BASE_DEFINITION_PATH'] = base_descriptor_file_v2
  subproc_env['COMPONENT_DESCRIPTOR_PATH'] = v2_outfile
  subproc_env['COMPONENT_NAME'] = component_name
  subproc_env['COMPONENT_VERSION'] = effective_version
  subproc_env['EFFECTIVE_VERSION'] = effective_version
  subproc_env['CURRENT_COMPONENT_REPOSITORY'] = ctx_repository_base_url
  subproc_env['OCM_CONFIG_PATH'] = ocm_config_file

  # pass predefined command to add dependencies for convenience purposes
  add_dependencies_cmd = ' '.join((
    'gardener-ci',
    'productutil_v2',
    'add_dependencies',
    '--descriptor-src-file', base_descriptor_file_v2,
    '--descriptor-out-file', base_descriptor_file_v2,
    '--component-version', effective_version,
    '--component-name', component_name,
  ))

  subproc_env['ADD_DEPENDENCIES_CMD'] = add_dependencies_cmd

  % for name, value in descriptor_trait.callback_env().items():
  subproc_env['${name}'] = '${value}'
  % endfor

  subprocess.run(
    [descriptor_script],
    check=True,
    cwd=descriptor_out_dir,
    env=subproc_env
  )

else:
  logger.info(
    f'no component_descriptor script found at {descriptor_script} - will use default'
  )
  with open(v2_outfile, 'w') as f:
    f.write(dump_component_descriptor_v2(base_descriptor_v2))
  logger.info(f'wrote OCM component descriptor: {v2_outfile=}')

have_cd = os.path.exists(v2_outfile)

if have_cd:
  # ensure the script actually created an output
  if not os.path.isfile(v2_outfile):
    fail(f'no descriptor file was found at: {v2_outfile=}')

  descriptor_v2 = cm.ComponentDescriptor.from_dict(
    ci.util.parse_yaml_file(v2_outfile)
  )
  logger.info(f'found component-descriptor (v2) at {v2_outfile=}:\n')
  print(dump_component_descriptor_v2(descriptor_v2))
else:
  print(f'XXX: did not find a component-descriptor at {v2_outfile=}')
  exit(1)

% if descriptor_trait.upload is comp_descr_trait.UploadMode.LEGACY:
  % if not (job_variant.has_trait('release') or job_variant.has_trait('update_component_deps')):
if snapshot_ctx_repository_base_url:
  if have_cd:
    snapshot_descriptor = cm.ComponentDescriptor.from_dict(
      ci.util.parse_yaml_file(v2_outfile)
    )
    if ctx_repository_base_url != snapshot_ctx_repository_base_url:
      repo_ctx = cm.OciOcmRepository(
        baseUrl=snapshot_ctx_repository_base_url,
        type=cm.AccessType.OCI_REGISTRY,
      )
      snapshot_descriptor.component.repositoryContexts.append(repo_ctx)

    cnudie.upload.upload_component_descriptor(
      snapshot_descriptor,
    )
    logger.info(f'uploaded component-descriptor to {ctx_repository_base_url}')
  % endif
% endif

# determine "bom-diff" (changed component references)
try:
  bom_diff = component_diff_since_last_release(
      component_descriptor=descriptor_v2,
      mapping_config=mapping_config,
  )
except:
  logger.warning('failed to determine component-diff')
  import traceback
  traceback.print_exc()
  bom_diff = None

if not bom_diff:
  logger.info('no differences in referenced components found since last release')
else:
  logger.info('component dependencies diff was written to dependencies.diff')
  dependencies_path = os.path.join(descriptor_out_dir, 'dependencies.diff')
  write_component_diff(
    component_diff=bom_diff,
    out_path=dependencies_path,
  )
  with open(dependencies_path) as f:
    print(f.read())
% if retention_policy:

logger.info('will honour retention-policy')
retention_policy = dacite.from_dict(
  data_class=version.VersionRetentionPolicies,
  data=${retention_policy},
  config=dacite.Config(cast=(enum.Enum,)),
)
pprint.pprint(retention_policy)

if retention_policy.dry_run:
  logger.info('dry-run - will only print versions to remove, but not actually remove them')
else:
  logger.info('!! will attempt to remove listed component-versions, according to policy')

logger.info('the following versions were identified for being purged')
component = descriptor_v2.component
oci_client = ccc.oci.oci_client()

lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
    ocm_repository_lookup=mapping_config,
)

for idx, component_id in enumerate(cnudie.purge.iter_componentversions_to_purge(
    component=component,
    policy=retention_policy,
    oci_client=oci_client,
)):
  if idx >= 64:
    print('will abort the purge, considering there seem to be more than 64 versions to cleanup')
    print('this is done to limit execution-time - the purge will continue on next execution')
    exit(0)
  print(f'{idx} {component_id.name}:{component_id.version}')
  if retention_policy.dry_run:
   continue
  component_to_purge = lookup(
    cm.ComponentIdentity(
      name=component.name,
      version=component_id.version,
    )
  )
  if not component_to_purge:
    logger.warning(f'{component.name}:{component_id.version} was not found - ignore')
    continue

  try:
   cnudie.purge.remove_component_descriptor_and_referenced_artefacts(
    component=component_to_purge,
    oci_client=oci_client,
    lookup=lookup,
    recursive=False,
   )
  except Exception as e:
   logger.warning(f'error occurred while trying to purge {component_id}: {e}')
   traceback.print_exc()
% else:
logger.info('no retention-policy was defined - will not purge component-descriptors')
% endif
</%def>

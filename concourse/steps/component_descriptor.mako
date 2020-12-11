<%def
  name="component_descriptor_step(job_step, job_variant, output_image_descriptors, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
descriptor_trait = job_variant.trait('component_descriptor')
main_repo = job_variant.main_repository()
main_repo_labels = main_repo.source_labels()
main_repo_path_env_var = main_repo.logical_name().replace('-', '_').upper() + '_PATH'
other_repos = [r for r in job_variant.repositories()
 if not r.is_main_repo()
]
ctx_repository_base_url = descriptor_trait.ctx_repository_base_url()

if job_variant.has_trait('image_alter'):
  image_alter_cfgs = job_variant.trait('image_alter').image_alter_cfgs()
else:
  image_alter_cfgs = ()
%>
import dataclasses
import git
import json
import os
import shutil
import stat
import subprocess
import sys
import yaml

import gci.componentmodel
cm = gci.componentmodel

from product.model import ComponentDescriptor, Component, ContainerImage, Relation
from product.util import ComponentDescriptorResolver
from ci.util import info, fail, parse_yaml_file, ctx
import product.v2

${step_lib('component_descriptor')}
${step_lib('component_descriptor_util')}

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

main_repo_path = os.path.abspath('${main_repo.resource_name()}')
main_git_repo = git.Repo(main_repo_path)
if not main_git_repo.head.is_valid():
  commit_hash = None
else:
  try:
    commit_hash = main_git_repo.head.commit.hexsha
  except:
    commit_hash = None

# create base descriptor filled with default values
base_descriptor_v2 = base_component_descriptor_v2(
    component_name_v2=component_name_v2,
    component_labels=component_labels,
    effective_version=effective_version,
    source_labels=${main_repo_labels},
    ctx_repository_base_url=ctx_repository_base_url,
    commit=commit_hash,
)
component_v2 = base_descriptor_v2.component

## XXX unify w/ injection-method used for main-repository
% for repository in other_repos:
component_v2.sources.append(
  cm.ComponentSource(
    name='${repository.name()}',
    type=cm.SourceType.GIT,
    access=cm.GithubAccess(
      type=cm.AccessType.GITHUB,
      repoUrl='${repository.repo_hostname()}/${repository.repo_path()}',
      ref='${repository.branch()}',
      commit=commit_hash,
    ),
    version=effective_version,
    labels=${repository.source_labels()},
  )
)
% endfor

# add own container image references
% for name, image_descriptor in output_image_descriptors.items():
component_v2.resources.append(
  cm.Resource(
    name='${name}',
    version=effective_version, # always inherited from component
    type=cm.ResourceType.OCI_IMAGE,
    relation=cm.ResourceRelation.LOCAL,
    access=cm.OciAccess(
      type=cm.AccessType.OCI_REGISTRY,
      imageReference='${image_descriptor.image_reference()}' + ':' + effective_version,
    ),
    labels=${image_descriptor.resource_labels()}
  ),
)
% endfor

base_descriptor_v1 = product.v2.convert_to_v1(component_descriptor_v2=base_descriptor_v2)
component = base_descriptor_v1.component((component_name, effective_version))
dependencies = component.dependencies()

# add container image references from patch_images trait
% for image_alter_cfg in image_alter_cfgs:
dependencies.add_container_image_dependency(
  ContainerImage.create(
    name='${image_alter_cfg.name()}',
    version='${image_alter_cfg.tgt_ref().rsplit(':', 1)[-1]}',
    image_reference='${image_alter_cfg.tgt_ref()}',
  )
)
% endfor

info('default component descriptor (v2):\n')
print(dump_component_descriptor_v2(base_descriptor_v2))
print('\n' * 2)

descriptor_out_dir = os.path.abspath('${job_step.output("component_descriptor_dir")}')
descriptor_path = os.path.join(
  descriptor_out_dir,
  component_descriptor_fname(schema_version=gci.componentmodel.SchemaVersion.V1),
)

v2_outfile = os.path.join(
  descriptor_out_dir,
  component_descriptor_fname(schema_version=gci.componentmodel.SchemaVersion.V2),
)

ctf_out_path = os.path.abspath(
  os.path.join(descriptor_out_dir, 'cnudie-transport-format.out')
)

descriptor_script = os.path.abspath(
  '${job_variant.main_repository().resource_name()}/.ci/${job_step.name}'
)
if not os.path.isfile(descriptor_script):
  info('no component_descriptor script found at {s} - will use default'.format(
    s=descriptor_script
    )
  )
  with open(descriptor_path, 'w') as f:
    yaml.dump(base_descriptor_v1.raw, f, indent=2)
  info(f'wrote component descriptor (v1): {descriptor_path=}')

  with open(v2_outfile, 'w') as f:
    f.write(dump_component_descriptor_v2(base_descriptor_v2))
  info(f'wrote component descriptor (v2): {v2_outfile=}')
  sys.exit(0)
else:
  is_executable = bool(os.stat(descriptor_script)[stat.ST_MODE] & stat.S_IEXEC)
  if not is_executable:
    fail('descriptor script file exists but is not executable: ' + descriptor_script)


# dump base_descriptor_v2 and pass it to descriptor script
base_descriptor_file_v2 = os.path.join(descriptor_out_dir, 'base_component_descriptor_v2')
with open(base_descriptor_file_v2, 'w') as f:
  f.write(dump_component_descriptor_v2(base_descriptor_v2))


subproc_env = os.environ.copy()
subproc_env['${main_repo_path_env_var}'] = main_repo_path
subproc_env['MAIN_REPO_DIR'] = main_repo_path
subproc_env['BASE_DEFINITION_PATH'] = base_descriptor_file_v2
subproc_env['COMPONENT_DESCRIPTOR_PATH'] = v2_outfile
subproc_env['COMPONENT_DESCRIPTOR_PATH_V1'] = descriptor_path
subproc_env['CTF_PATH'] = ctf_out_path
subproc_env['COMPONENT_NAME'] = component_name
subproc_env['COMPONENT_VERSION'] = effective_version

# pass predefined command to add dependencies for convenience purposes
add_dependencies_cmd = ' '.join((
  '/cc/utils/cli.py',
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

have_ctf = os.path.exists(ctf_out_path)
have_cd = os.path.exists(v2_outfile)

if not have_ctf ^ have_cd:
    fail(f'exactly one of {ctf_out_path=}, {v2_outfile=} must exist')
elif have_cd:
  # ensure the script actually created an output
  if not os.path.isfile(v2_outfile):
    fail(f'no descriptor file was found at: {v2_outfile=}')

  descriptor_v2 = cm.ComponentDescriptor.from_dict(
    ci.util.parse_yaml_file(v2_outfile)
  )
  print(f'found component-descriptor (v2) at {v2_outfile=}')

  # convert back to v1 for backwards compatibility for now
  descriptor_v1 = product.v2.convert_to_v1(component_descriptor_v2=descriptor_v2)
  with open(descriptor_path, 'w') as f:
    yaml.dump(descriptor_v1.raw, f)
  info(f'created v1-version of cd at {descriptor_path=} from v2-descriptor')

  descriptor = ComponentDescriptor.from_dict(parse_yaml_file(descriptor_path))
elif have_ctf:
  fail('CTF not yet supported')

cfg_factory = ctx().cfg_factory()
info('resolving dependencies')

resolver = ComponentDescriptorResolver(
  cfg_factory=cfg_factory,
)
descriptor = resolver.resolve_component_references(descriptor)
descriptor_str = yaml.dump(json.loads(json.dumps(descriptor.raw)))

info('effective component descriptor with resolved dependencies:')
print(descriptor_str)
with open(descriptor_path, 'w') as f:
  f.write(descriptor_str)

# determine "bom-diff" (changed component references)
bom_diff = component_diff_since_last_release(
    component_name=component_name,
    component_version=effective_version,
    component_descriptor=descriptor,
    ctx_repo_url=ctx_repository_base_url,
    cfg_factory=cfg_factory,
)
if not bom_diff:
  info('no differences in referenced components found since last release')
else:
  info('component dependencies diff was written to dependencies.diff')
  dependencies_path = os.path.join(descriptor_out_dir, 'dependencies.diff')
  write_component_diff(
    component_diff=bom_diff,
    out_path=dependencies_path,
  )
  with open(dependencies_path) as f:
    print(f.read())
</%def>

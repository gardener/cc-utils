<%def
  name="component_descriptor_step(job_step, job_variant, output_image_descriptors, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
descriptor_trait = job_variant.trait('component_descriptor')
main_repo = job_variant.main_repository()
main_repo_path_env_var = main_repo.logical_name().replace('-', '_').upper() + '_PATH'

policies = descriptor_trait.validation_policies()

if job_variant.has_trait('image_alter'):
  image_alter_cfgs = job_variant.trait('image_alter').image_alter_cfgs()
else:
  image_alter_cfgs = ()
%>
import json
import os
import shutil
import stat
import subprocess
import sys
import yaml

from product.model import ComponentDescriptor, Component, ContainerImage
from product.util import ComponentDescriptorResolver
from ci.util import info, fail, parse_yaml_file, ctx

${step_lib('component_descriptor')}

# retrieve effective version
version_file_path = os.path.join(
  '${job_step.input('version_path')}',
  'version',
)
with open(version_file_path) as f:
  effective_version = f.read().strip()

component_name = '${descriptor_trait.component_name()}'

# create base descriptor filled with default values
base_descriptor = ComponentDescriptor()
component = Component.create(
  name='${descriptor_trait.component_name()}',
  version=effective_version,
)
base_descriptor.add_component(component)

# add own container image references
dependencies = component.dependencies()
% for name, image_descriptor in output_image_descriptors.items():
dependencies.add_container_image_dependency(
  ContainerImage.create(
    name='${name}',
    version=effective_version,
    image_reference='${image_descriptor.image_reference()}' + ':' + effective_version
  )
)
% endfor

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

info('default component descriptor:\n')
print(yaml.dump(base_descriptor.raw, indent=2))

descriptor_out_dir = os.path.abspath('${job_step.output("component_descriptor_dir")}')
descriptor_path = os.path.join(descriptor_out_dir, 'component_descriptor')

descriptor_script = os.path.abspath(
  '${job_variant.main_repository().resource_name()}/.ci/${job_step.name}'
)
if not os.path.isfile(descriptor_script):
  info('no component_descriptor script found at {s} - will use default'.format(
    s=descriptor_script
    )
  )
  with open(descriptor_path, 'w') as f:
    yaml.dump(base_descriptor.raw, f, indent=2)
  info('wrote component descriptor: ' + descriptor_path)
  sys.exit(0)
else:
  is_executable = bool(os.stat(descriptor_script)[stat.ST_MODE] & stat.S_IEXEC)
  if not is_executable:
    fail('descriptor script file exists but is not executable: ' + descriptor_script)


# dump base_descriptor and pass it to descriptor script via env var
base_descriptor_file = os.path.join(descriptor_out_dir, 'base_component_descriptor')
with open(base_descriptor_file, 'w') as f:
  json.dump(base_descriptor.raw, f, indent=2)

# make main repository path absolute
main_repo_path = os.path.abspath('${main_repo.resource_name()}')

subproc_env = os.environ.copy()
subproc_env['${main_repo_path_env_var}'] = main_repo_path
subproc_env['MAIN_REPO_DIR'] = main_repo_path
subproc_env['BASE_DEFINITION_PATH'] = base_descriptor_file
subproc_env['COMPONENT_DESCRIPTOR_PATH'] = descriptor_path
subproc_env['COMPONENT_NAME'] = component_name
subproc_env['COMPONENT_VERSION'] = effective_version

# pass predefined command to add dependencies for convenience purposes
add_dependencies_cmd = ' '.join((
  '/cc/utils/cli.py',
  'productutil',
  'add_dependencies',
  '--descriptor-src-file', base_descriptor_file,
  '--descriptor-out-file', base_descriptor_file,
  '--component-version', effective_version,
  '--component-name', component_name,
% for policy in policies:
  '--validation-policies', '${policy}',
% endfor
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

# ensure the script actually created an output
if not os.path.isfile(descriptor_path):
  fail('no descriptor file was found at: ' + descriptor_path)

descriptor = ComponentDescriptor.from_dict(parse_yaml_file(descriptor_path))
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

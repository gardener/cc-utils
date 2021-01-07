<%def name="prepare_step(job_step, job_variant, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
# TODO: actually, we would require dedicated prepare steps for each image
input_step_names = set()
tag_dir = job_step.output('tag_path')
publish_trait = job_variant.trait('publish')

for image_descriptor in publish_trait.dockerimages():
  input_step_names.update(image_descriptor.input_steps())

main_repo = job_variant.main_repository()


input_dirs = set()
for input_step_name in input_step_names:
  step = job_variant.step(input_step_name)
  exposed_dir = step.output_dir()
  if not exposed_dir:
    raise ValueError('step must expose output_dir: ' + str(input_step_name))
  input_dirs.add(exposed_dir)

if job_variant.has_trait('version'):
  version_trait = job_variant.trait('version')
  inject_effective_version = version_trait.inject_effective_version()
else:
  inject_effective_version = False

%>
cp -Tfr ${main_repo.resource_name()} ${job_step.output('image_path')}
% if inject_effective_version:
# patch-in effective version
cp "${job_step.input('version_path')}/version" \
   "${job_step.output('image_path')}/${version_trait.versionfile_relpath()}"
% endif
<% # caveat: mako will _dedent_ contents inside the for loop
%>
% for input_dir in input_dirs:
    find "${input_dir}"
    cp -Tfr "${input_dir}" "${job_step.output('image_path')}"
% endfor
% for image_descriptor in publish_trait.dockerimages():
<%
  effective_version = f'$(cat "{job_step.input("version_path")}/version")'
  out_path = f'{tag_dir}/{image_descriptor.name()}.tag'
  tag_template = image_descriptor.tag_template()
%>
export EFFECTIVE_VERSION="${effective_version}"; eval "echo "${tag_template}"" > "${out_path}"
% endfor
</%def>

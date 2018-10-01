<%def name="prepare_step(job_step, job_variant, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
# TODO: actually, we would require dedicated prepare steps for each image
input_step_names = set()
for image_descriptor in job_variant.trait('publish').dockerimages():
  input_step_names.update(image_descriptor.input_steps())

main_repo = job_variant.main_repository()

input_dirs = set()
for input_step_name in input_step_names:
  step = job_variant.step(input_step_name)
  exposed_dir = step.output_dir()
  if not exposed_dir:
    raise ValueError('step must expose output_dir: ' + str(input_step_name))
  input_dirs.add(exposed_dir)
%>
cp -Tfr ${main_repo.resource_name()} ${job_step.output('image_path')}
<% # caveat: mako will _dedent_ contents inside the for loop
%>
% for input_dir in input_dirs:
    find "${input_dir}"
    cp -Tfr "${input_dir}" "${job_step.output('image_path')}"
% endfor
</%def>

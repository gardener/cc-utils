<%def name="publish_step(job_step, job_variant)", filter="trim">
<%
publish_trait = job_variant.trait('publish')
%>
% for descriptor in publish_trait.dockerimages():
<%
import os
build_dir = job_step.input('image_path')
if descriptor.builddir_relpath():
  build_dir = os.path.join(build_dir, descriptor.builddir_relpath())
dockerfile = os.path.join(build_dir, descriptor.dockerfile_relpath())
%>
      # noop
- put: ${descriptor.resource_name()}
  params:
    build: ${build_dir}
    dockerfile: ${dockerfile}
    tag_file: ${job_step.input('version_path')}/version
% if descriptor.target_name():
    target_name: ${descriptor.target_name()}
% endif
% if descriptor.tag_as_latest():
    tag_as_latest: ${descriptor.tag_as_latest()}
% endif
% endfor
</%def>

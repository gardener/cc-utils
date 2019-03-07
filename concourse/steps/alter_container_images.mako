<%def
  name="alter_container_images_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
main_repo = job_variant.main_repository()
repo_path = main_repo.resource_name()

image_alter_trait = job_variant.trait('image_alter')
image_alter_cfgs = image_alter_trait.image_alter_cfgs()
component_trait = job_variant.trait('component_descriptor')
%>
import os
${step_lib('alter_container_images')}

% for alter_cfg in image_alter_cfgs:
alter_image(
  src_ref='${alter_cfg.src_ref()}',
  tgt_ref='${alter_cfg.tgt_ref()}',
  filter_path_file=os.path.join(
    CC_ROOT_DIR,
    '${repo_path}',
    '${alter_cfg.rm_paths_file()}'
  ),
)
% endfor
</%def>

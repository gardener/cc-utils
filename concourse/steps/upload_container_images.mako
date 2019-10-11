<%def
  name="upload_container_images_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()

upload_trait = job_variant.trait('image_upload')
upload_registry_prefix = upload_trait.upload_registry_prefix()
filter_cfg = upload_trait.filters()
component_trait = job_variant.trait('component_descriptor')
%>
import concurrent.futures
import functools
import os
import tabulate

import ci.util
import product.model
import product.util
import protecode.util

${step_lib('images')}
${step_lib('upload_images')}
${step_lib('component_descriptor_util')}

cfg_factory = ci.util.ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

upload_registry_prefix = '${upload_registry_prefix}'

# print configuration
print(tabulate.tabulate(
  (
    ('Image Filter (include)', ${filter_cfg.include_image_references()}),
    ('Image Filter (exclude)', ${filter_cfg.exclude_image_references()}),
    ('Upload Registry prefix', upload_registry_prefix),
  ),
))

component_descriptor_path = os.path.join(
  ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
  'component_descriptor'
)

component_descriptor = parse_component_descriptor()

filter_function = create_composite_filter_function(
  include_image_references=${filter_cfg.include_image_references()},
  exclude_image_references=${filter_cfg.exclude_image_references()},
  include_image_names=${filter_cfg.include_image_names()},
  exclude_image_names=${filter_cfg.exclude_image_names()},
  include_component_names=${filter_cfg.include_component_names()},
  exclude_component_names=${filter_cfg.exclude_component_names()},
)

image_references = [
  container_image.image_reference()
  for component, container_image
  in product.util._enumerate_effective_images(
    component_descriptor=component_descriptor,
  )
  if filter_function(component, container_image)
]
parallel_jobs = ${upload_trait.parallel_jobs()}
executor = concurrent.futures.ThreadPoolExecutor(max_workers=parallel_jobs)

reupload_fun = functools.partial(republish_image, tgt_prefix=upload_registry_prefix, mangle=True)

for from_ref, to_ref in executor.map(reupload_fun, image_references):
  print(f'uploaded {from_ref} -> {to_ref}')

# download images again to ensure GCR vulnerability scanning (see method docstring for more info)
protecode.util.download_images(
  component_descriptor=component_descriptor,
  upload_registry_prefix=upload_registry_prefix,
  image_reference_filter=filter_function,
  parallel_jobs=parallel_jobs,
)
</%def>

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

import product.model
import product.util
import protecode.util
import util

${step_lib('images')}
${step_lib('upload_images')}
${step_lib('component_descriptor_util')}

cfg_factory = util.ctx().cfg_factory()
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
  util.check_env('COMPONENT_DESCRIPTOR_DIR'),
  'component_descriptor'
)

component_descriptor = parse_component_descriptor()

image_filter = image_reference_filter(
  include_regexes=${filter_cfg.include_image_references()},
  exclude_regexes=${filter_cfg.exclude_image_references()},
)

image_references = [
  ci.image_reference()
  for _, ci
  in product.util._enumerate_images(
    component_descriptor=component_descriptor,
    image_reference_filter=image_filter,
  )
]

executor = concurrent.futures.ThreadPoolExecutor(max_workers=${upload_trait.parallel_jobs()})

reupload_fun = functools.partial(republish_image, tgt_prefix=upload_registry_prefix, mangle=True)

for from_ref, to_ref in executor.map(reupload_fun, image_references):
  print(f'uploaded {from_ref} -> {to_ref}')
</%def>

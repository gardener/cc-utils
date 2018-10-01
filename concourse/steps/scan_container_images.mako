<%def
  name="scan_container_images_step(job_step, job_variant, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()

image_scan_trait = job_variant.trait('image_scan')
%>

import pathlib

import product.model
import protecode.util
import util

# XXX suppress warnings for sap-ca
# (is installed in truststore in cc-job-image, but apparently not honoured by httlib2)
import urllib3
urllib3.disable_warnings()

cfg_factory = util.ctx().cfg_factory()
protecode_cfg = cfg_factory.protecode('${image_scan_trait.protecode_cfg_name()}')

component_descriptor_file = pathlib.Path(
  util.check_env('COMPONENT_DESCRIPTOR_DIR'),
  'component_descriptor'
)

component_descriptor = product.model.Product.from_dict(
  raw_dict=util.parse_yaml_file(component_descriptor_file)
)

protecode.util.upload_images(
  protecode_cfg=protecode_cfg,
  product_descriptor=component_descriptor,
  protecode_group_id=int(${image_scan_trait.protecode_group_id()}),
  parallel_jobs=${image_scan_trait.parallel_jobs()},
  cve_threshold=${image_scan_trait.cve_threshold()},
)
</%def>

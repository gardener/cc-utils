<%def
  name="scan_container_images_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()

image_scan_trait = job_variant.trait('image_scan')
filter_cfg = image_scan_trait.filters()
component_trait = job_variant.trait('component_descriptor')
%>
import sys
import pathlib

import mailutil
import product.model
import protecode.util
import util

from product.scanning import ProcessingMode

${step_lib('scan_container_images')}

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

processing_mode = ProcessingMode('${image_scan_trait.processing_mode()}')

image_filter = image_reference_filter(
  include_regexes=${filter_cfg.include_image_references()},
  exclude_regexes=${filter_cfg.exclude_image_references()},
)

relevant_results = protecode.util.upload_images(
  protecode_cfg=protecode_cfg,
  product_descriptor=component_descriptor,
  processing_mode=processing_mode,
  protecode_group_id=int(${image_scan_trait.protecode_group_id()}),
  parallel_jobs=${image_scan_trait.parallel_jobs()},
  cve_threshold=${image_scan_trait.cve_threshold()},
  image_reference_filter=image_filter,
)
if not relevant_results:
  sys.exit(0)
email_recipients = ${image_scan_trait.email_recipients}
if not email_recipients:
  util.warning('Relevant Vulnerabilities were found, but there are no mail recipients configured')
  sys.exit(0)

# notify about critical vulnerabilities

# component_name identifies the landscape that has been scanned
component_name = component_trait.component_name()
body = 'The following components were found to contain vulnerabilities:\n'
body += tabulate.tabulate(
  map(lambda r: (r[0].display_name(), r[1]), relevant_results),
  headers=('Component Name', 'Greatest CVE'),
)

mailutil._send_mail(
  email_cfg=cfg_set.email(),
  recipients=email_recipients,
  mail_template=body,
  subject=f'[Action Required] landscape {component_name} has critical Vulnerabilities',
)
</%def>

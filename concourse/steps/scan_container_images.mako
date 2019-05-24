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
protecode_scan = image_scan_trait.protecode()
# XXX: protecode soon to become optional!

# XXX: consider moving upload-images to separate trait (or cfg in this trait)
upload_registry_prefix = protecode_scan.upload_registry_prefix()
filter_cfg = image_scan_trait.filters()
component_trait = job_variant.trait('component_descriptor')
%>
import functools
import os
import sys
import tabulate
import textwrap

import mailutil
import product.model
import product.util
import protecode.util
import util

from product.scanning import ProcessingMode

${step_lib('scan_container_images')}
${step_lib('images')}
${step_lib('component_descriptor_util')}

cfg_factory = util.ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

% if not protecode_scan.protecode_cfg_name():
protecode_cfg = cfg_factory.protecode()
% else:
protecode_cfg = cfg_factory.protecode('${protecode_scan.protecode_cfg_name()}')
% endif

protecode_group_id = int(${protecode_scan.protecode_group_id()})
protecode_group_url = f'{protecode_cfg.api_url()}/group/{protecode_group_id}/'

# print configuration
print(tabulate.tabulate(
  (
    ('Protecode target group id', str(protecode_group_id)),
    ('Protecode group URL', protecode_group_url),
    ('Protecode reference group IDs', ${protecode_scan.reference_protecode_group_ids()}),
    ('Image Filter (include)', ${filter_cfg.include_image_references()}),
    ('Image Filter (exclude)', ${filter_cfg.exclude_image_references()}),
% if upload_registry_prefix:
    ('Upload Registry prefix', '${upload_registry_prefix}'),
% endif
  ),
))

component_descriptor = parse_component_descriptor()

processing_mode = ProcessingMode('${protecode_scan.processing_mode()}')

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

relevant_results, license_report = protecode.util.upload_images(
  protecode_cfg=protecode_cfg,
  product_descriptor=component_descriptor,
  processing_mode=processing_mode,
  protecode_group_id=protecode_group_id,
  parallel_jobs=${protecode_scan.parallel_jobs()},
  cve_threshold=${protecode_scan.cve_threshold()},
  image_reference_filter=image_filter,
% if upload_registry_prefix:
  upload_registry_prefix='${upload_registry_prefix}',
% endif
  reference_group_ids=${protecode_scan.reference_protecode_group_ids()},
)

def create_license_report(license_report):
  def to_table_row(upload_result, licenses):
    component_name = upload_result.result.display_name()
    license_names = {license.name() for license in licenses}
    license_names_str = ', '.join(license_names)
    yield (component_name, license_names_str)

  license_lines = [
    to_table_row(upload_result, licenses)
    for upload_result, licenses in license_report
  ]

  print(tabulate.tabulate(
    license_lines,
    headers=('Component Name', 'Licenses'),
    )
  )

  return license_lines

util.info('running virus scan for all container images')
images_with_potential_viruses = tuple(virus_scan_images(image_references))
if images_with_potential_viruses:
  util.warning('Potential viruses found:')
  util.warning('\n'.join(map(str, images_with_potential_viruses)))
else:
  util.info(f'{len(image_references)} image(s) scanned for virus signatures w/o any matches')

# XXX also include in email
report_lines = create_license_report(license_report=license_report)

if not relevant_results and not images_with_potential_viruses:
  sys.exit(0)
email_recipients = ${image_scan_trait.email_recipients()}

email_recipients = tuple(
  mail_recipients(
    notification_policy='${image_scan_trait.notify().value}'
    root_component_name='${component_trait.component_name()}',
    protecode_cfg=protecode_cfg,
    protecode_group_id=protecode_group_id,
    protecode_group_url=protecode_group_url,
    email_recipients=email_recipients,
  )
)

for email_recipient in email_recipients:
  email_recipient.add_protecode_results(results=relevant_results)
  email_recipient.add_clamav_results(results=images_with_potential_viruses)

  body = email_recipients.mail_body()
  email_addresses = email_recipients.resolve_recipients()


  # component_name identifies the landscape that has been scanned
  component_name = "${component_trait.component_name()}"


  # notify about critical vulnerabilities
  mailutil._send_mail(
    email_cfg=cfg_set.email(),
    recipients=email_addresses,
    mail_template=body,
    subject=f'[Action Required] landscape {component_name} has critical Vulnerabilities',
    mimetype='html',
  )
  util.info('sent notification emails to: ' + ','.join(email_addresses))
</%def>

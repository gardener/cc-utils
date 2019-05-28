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
clam_av = image_scan_trait.clam_av()

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

component_descriptor = parse_component_descriptor()

protecode_results = ()
% if protecode_scan:
  % if not protecode_scan.protecode_cfg_name():
protecode_cfg = cfg_factory.protecode()
  % else:
protecode_cfg = cfg_factory.protecode('${protecode_scan.protecode_cfg_name()}')
  % endif

protecode_group_id = ${protecode_scan.protecode_group_id()}
protecode_group_url = f'{protecode_cfg.api_url()}/group/{protecode_group_id}/'

protecode_results, license_report = protecode_scan(
  protecode_cfg=protecode_cfg,
  protecode_group_id = protecode_group_id,
  protecode_group_url = protecode_group_url,
  product_descriptor = component_descriptor,
  reference_protecode_group_ids = ${protecode_scan.reference_protecode_group_ids()},
  processing_mode = ProcessingMode('${protecode_scan.processing_mode()}'),
  parallel_jobs=${protecode_scan.parallel_jobs()},
  cve_threshold=${protecode_scan.cve_threshold()},
  include_image_references=${filter_cfg.include_image_references()},
  exclude_image_references=${filter_cfg.exclude_image_references()},
)
% endif

images_with_potential_viruses = ()

% if clam_av:

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

util.info('running virus scan for all container images')
images_with_potential_viruses = tuple(virus_scan_images(image_references))
if images_with_potential_viruses:
  util.warning('Potential viruses found:')
  util.warning('\n'.join(map(str, images_with_potential_viruses)))
else:
  util.info(f'{len(image_references)} image(s) scanned for virus signatures w/o any matches')

% endif

if not protecode_results and not images_with_potential_viruses:
  sys.exit(0)

email_recipients = ${image_scan_trait.email_recipients()}

email_recipients = tuple(
  mail_recipients(
    notification_policy='${image_scan_trait.notify().value}',
    root_component_name='${component_trait.component_name()}',
% if protecode_scan:
    protecode_cfg=protecode_cfg,
    protecode_group_id=protecode_group_id,
    protecode_group_url=protecode_group_url,
% endif
    cfg_set=cfg_set,
    email_recipients=email_recipients,
    components=component_descriptor.components(),
  )
)

for email_recipient in email_recipients:
  email_recipient.add_protecode_results(results=protecode_results)
  email_recipient.add_clamav_results(results=images_with_potential_viruses)

  if not email_recipient.has_results():
    util.info(f'skipping {email_recipient}, since there are not relevant results')
    continue

  body = email_recipient.mail_body()
  email_addresses = set(email_recipient.resolve_recipients())

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
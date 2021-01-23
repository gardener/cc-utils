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

import gci.componentmodel as cm

# debugging (dump stacktrace on error-signals)
import faulthandler
faulthandler.enable() # print stacktraces upon fatal signals
# end of debugging block

import ctx
try:
  ctx.configure_default_logging()
except:
  pass
import ci.util
import cnudie.retrieve
import mailutil
import product.util
import protecode.util


from concourse.model.traits.image_scan import Notify
from protecode.model import CVSSVersion
from protecode.scanning_util import ProcessingMode

${step_lib('scan_container_images')}
${step_lib('images')}
${step_lib('component_descriptor_util')}

cfg_factory = ci.util.ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

component_descriptor = parse_component_descriptor()

filter_function = create_composite_filter_function(
  include_image_references=${filter_cfg.include_image_references()},
  exclude_image_references=${filter_cfg.exclude_image_references()},
  include_image_names=${filter_cfg.include_image_names()},
  exclude_image_names=${filter_cfg.exclude_image_names()},
  include_component_names=${filter_cfg.include_component_names()},
  exclude_component_names=${filter_cfg.exclude_component_names()},
)

% if not protecode_scan.protecode_cfg_name():
protecode_cfg = cfg_factory.protecode()
% else:
protecode_cfg = cfg_factory.protecode('${protecode_scan.protecode_cfg_name()}')
% endif

protecode_group_id = ${protecode_scan.protecode_group_id()}
protecode_group_url = f'{protecode_cfg.api_url()}/group/{protecode_group_id}/'

print_protecode_info_table(
  protecode_group_id = protecode_group_id,
  reference_protecode_group_ids = ${protecode_scan.reference_protecode_group_ids()},
  protecode_group_url = protecode_group_url,
  cvss_version = CVSSVersion('${protecode_scan.cvss_version().value}'),
  include_image_references=${filter_cfg.include_image_references()},
  exclude_image_references=${filter_cfg.exclude_image_references()},
  include_image_names=${filter_cfg.include_image_names()},
  exclude_image_names=${filter_cfg.exclude_image_names()},
  include_component_names=${filter_cfg.include_component_names()},
  exclude_component_names=${filter_cfg.exclude_component_names()},
)

ci.util.info('running protecode scan for all components')
results_above_threshold, results_below_threshold, license_report = protecode.util.upload_grouped_images(
  protecode_cfg=protecode_cfg,
  protecode_group_id = protecode_group_id,
  component_descriptor = component_descriptor,
  reference_group_ids = ${protecode_scan.reference_protecode_group_ids()},
  processing_mode = ProcessingMode('${protecode_scan.processing_mode().value}'),
  parallel_jobs=${protecode_scan.parallel_jobs()},
  cve_threshold=${protecode_scan.cve_threshold()},
  image_reference_filter=filter_function,
  cvss_version = CVSSVersion('${protecode_scan.cvss_version().value}'),
)
ci.util.info('preparing license report for protecode results')
print_license_report(license_report)

allowed_licenses = ${protecode_scan.allowed_licenses()}
prohibited_licenses = ${protecode_scan.prohibited_licenses()}

updated_license_report = list(
  determine_rejected_licenses(license_report, allowed_licenses, prohibited_licenses)
)

# only include results below threshold if email recipients are explicitly configured
notification_policy = Notify('${image_scan_trait.notify().value}')
if notification_policy is not Notify.EMAIL_RECIPIENTS:
  results_below_threshold = []

if not (
  results_above_threshold
  or results_below_threshold
  or updated_license_report
):
  print('nothing to report - early-exiting')
  sys.exit(0)

email_recipients = ${image_scan_trait.email_recipients()}

components = tuple(cnudie.retrieve.components(component=component_descriptor))

email_recipients = tuple(
  mail_recipients(
    notification_policy='${image_scan_trait.notify().value}',
    root_component_name='${component_trait.component_name()}',
    protecode_cfg=protecode_cfg,
    protecode_group_id=protecode_group_id,
    protecode_group_url=protecode_group_url,
    cvss_version=CVSSVersion('${protecode_scan.cvss_version().value}'),
    cfg_set=cfg_set,
    email_recipients=email_recipients,
    components=components
  )
)

print(f'Components: {len(components)}   Mail recipients: {len(email_recipients)}')

for email_recipient in email_recipients:
  print(f'Preparing email recipients for {email_recipient._recipients_component}')
  email_recipient.add_protecode_results(
    relevant_results=results_above_threshold,
    results_below_threshold=results_below_threshold,
  )
  email_recipient.add_license_scan_results(results=updated_license_report)

  if not email_recipient.has_results():
    ci.util.info(f'skipping {email_recipient}, since there are no relevant results')
    continue

  body = email_recipient.mail_body()
  email_addresses = set(email_recipient.resolve_recipients())

  # XXX disable pdf-attachments for now
  if False and notification_policy is Notify.COMPONENT_OWNERS:
    attachments = email_recipient.pdf_report_attachments()
  else:
    attachments = []

  # component_name identifies the landscape that has been scanned
  component_name = "${component_trait.component_name()}"

  if not email_addresses:
    ci.util.warning(f'no email addresses could be retrieved for {component_name}')
    continue

  import traceback
  # notify about critical vulnerabilities
  try:
    mailutil._send_mail(
      email_cfg=cfg_set.email(),
      recipients=email_addresses,
      attachments=attachments,
      mail_template=body,
      subject=f'[Action Required] landscape {component_name} has critical Vulnerabilities',
      mimetype='html',
    )
    ci.util.info('sent notification emails to: ' + ','.join(email_addresses))
  except:
    traceback.print_exc()
    ci.util.warning(f'error whilst trying to send notification-mails for {component_name}')
</%def>

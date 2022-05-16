<%def
  name="scan_container_images_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
import dataclasses

main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()

image_scan_trait = job_variant.trait('image_scan')
protecode_scan = image_scan_trait.protecode()
clam_av = image_scan_trait.clam_av()

filter_cfg = image_scan_trait.filters()
component_trait = job_variant.trait('component_descriptor')

issue_tgt_repo_url = image_scan_trait.overwrite_github_issues_tgt_repository_url()
github_issue_template = image_scan_trait.github_issue_template()
github_issue_labels_to_preserve = image_scan_trait.github_issue_labels_to_preserve()
%>
import functools
import logging
import os
import sys
import tabulate
import textwrap

import dacite

import gci.componentmodel as cm

# debugging (dump stacktrace on error-signals)
import faulthandler
faulthandler.enable() # print stacktraces upon fatal signals
# end of debugging block

import ci.log
try:
  ci.log.configure_default_logging()
except:
  pass
import ci.util
import concourse.model.traits.image_scan as image_scan
import cnudie.retrieve
import mailutil
import product.util
import protecode.util


from concourse.model.traits.image_scan import Notify
from protecode.model import CVSSVersion
from protecode.scanning_util import ProcessingMode

logger = logging.getLogger('scan_container_images.step')

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

logger.info('running protecode scan for all components')
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
logger.info('preparing license report for protecode results')
print_license_report(license_report)

allowed_licenses = ${protecode_scan.allowed_licenses()}
prohibited_licenses = ${protecode_scan.prohibited_licenses()}

updated_license_report = list(
  determine_rejected_licenses(license_report, allowed_licenses, prohibited_licenses)
)

# only include results below threshold if email recipients are explicitly configured
notification_policy = Notify('${image_scan_trait.notify().value}')

if notification_policy is Notify.NOBODY:
  print("Notification policy set to 'nobody', exiting")
  sys.exit(0)

if all ((
  not results_above_threshold,
  not results_below_threshold,
  not updated_license_report,
)):
  print('nothing to report - early-exiting')
  sys.exit(0)

% if github_issue_template:
github_issue_template_cfg = dacite.from_dict(
  data_class=image_scan.GithubIssueTemplateCfg,
  data=${dataclasses.asdict(github_issue_template)},
)
% endif

if notification_policy is Notify.GITHUB_ISSUES:
  create_or_update_github_issues(
    results_to_report=results_above_threshold,
    results_to_discard=results_below_threshold,
% if issue_tgt_repo_url:
    issue_tgt_repo_url='${issue_tgt_repo_url}',
% endif
% if github_issue_template:
    github_issue_template_cfg=github_issue_template_cfg,
% endif
% if github_issue_labels_to_preserve:
    preserve_labels_regexes=${github_issue_labels_to_preserve},
% endif
    delivery_svc_endpoints=ccc.delivery.endpoints(cfg_set=cfg_set),
  )
  print(f'omitting email-sending, as notification-method was set to github-issues')
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

## order by max. severity; highest first (descending)
## type: Iterable[Tuple[ScanResult, float]] (written in comment to save imports)
results_above_threshold = sorted(
  results_above_threshold,
  key=lambda x: x.greatest_cve_score,
  reverse=True,
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
    logger.info(f'skipping {email_recipient=}, since there are no relevant results')
    continue

  body = email_recipient.mail_body()
  email_addresses = set(email_recipient.resolve_recipients())

  # component_name identifies the landscape that has been scanned
  component_name = "${component_trait.component_name()}"

  if not email_addresses:
    logger.warning(f'no email addresses could be determined for {component_name=}')
    continue

  import traceback
  # notify about critical vulnerabilities
  try:
    mailutil._send_mail(
      email_cfg=cfg_set.email(),
      recipients=email_addresses,
      mail_template=body,
      subject=f'[Action Required] landscape {component_name} has critical Vulnerabilities',
      mimetype='html',
    )
    logger.info(f'sent notification emails to: {email_addresses=}')
  except:
    traceback.print_exc()
    logger.warning(f'error whilst trying to send notification-mails for {component_name=}')
</%def>

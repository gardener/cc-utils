<%def
  name="scan_container_images_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
import dataclasses
import ci.util

image_scan_trait = job_variant.trait('image_scan')
issue_policies = image_scan_trait.issue_policies()
protecode_scan = image_scan_trait.protecode()
clam_av = image_scan_trait.clam_av()

filter_cfg = image_scan_trait.filters()

license_cfg = image_scan_trait.licenses()

issue_tgt_repo_url = image_scan_trait.overwrite_github_issues_tgt_repository_url()
if issue_tgt_repo_url:
  parsed_repo_url = ci.util.urlparse(issue_tgt_repo_url)
  tgt_repo_org, tgt_repo_name = parsed_repo_url.path.strip('/').split('/')


github_issue_templates = image_scan_trait.github_issue_templates()
github_issue_labels_to_preserve = image_scan_trait.github_issue_labels_to_preserve()
%>
import logging
import sys

import dacite

# debugging (dump stacktrace on error-signals)
import faulthandler
faulthandler.enable() # print stacktraces upon fatal signals
# end of debugging block

import ccc.github
import ci.log
ci.log.configure_default_logging()
import ci.util
import concourse.model.traits.image_scan as image_scan
import delivery.client
import github.compliance.report
import protecode.util


from concourse.model.traits.image_scan import Notify
from protecode.model import (
  CVSSVersion,
  ProcessingMode,
)

logger = logging.getLogger('scan_container_images.step')

${step_lib('scan_container_images')}
${step_lib('images')}
${step_lib('component_descriptor_util')}

cfg_factory = ci.util.ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

component_descriptor = parse_component_descriptor()

image_filter_function = create_composite_filter_function(
  include_image_references=${filter_cfg.include_image_references()},
  exclude_image_references=${filter_cfg.exclude_image_references()},
  include_image_names=${filter_cfg.include_image_names()},
  exclude_image_names=${filter_cfg.exclude_image_names()},
  include_component_names=${filter_cfg.include_component_names()},
  exclude_component_names=${filter_cfg.exclude_component_names()},
  include_component_versions=${filter_cfg.include_component_versions()},
  exclude_component_versions=${filter_cfg.exclude_component_versions()},
)

tar_filter_function = create_composite_filter_function(
  include_image_references=(),
  exclude_image_references=(),
  include_image_names=(),
  exclude_image_names=(),
  include_component_names=${filter_cfg.include_component_names()},
  exclude_component_names=${filter_cfg.exclude_component_names()},
  include_component_versions=${filter_cfg.include_component_versions()},
  exclude_component_versions=${filter_cfg.exclude_component_versions()},
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

cve_threshold = ${protecode_scan.cve_threshold()}

logger.info('running protecode scan for all components')
results_generator = protecode.util.upload_grouped_images(
  protecode_cfg=protecode_cfg,
  protecode_group_id = protecode_group_id,
  component_descriptor = component_descriptor,
  reference_group_ids = ${protecode_scan.reference_protecode_group_ids()},
  processing_mode = ProcessingMode('${protecode_scan.processing_mode().value}'),
  parallel_jobs=${protecode_scan.parallel_jobs()},
  cve_threshold=cve_threshold,
  image_filter_function=image_filter_function,
  tar_filter_function=tar_filter_function,
)
results = tuple(results_generator)

% if license_cfg:
license_cfg = dacite.from_dict(
  data_class=image_scan.LicenseCfg,
  data=${dataclasses.asdict(license_cfg)},
)
% else:
license_cfg = None
% endif


# only include results below threshold if email recipients are explicitly configured
notification_policy = Notify('${image_scan_trait.notify().value}')

if notification_policy is Notify.NOBODY:
  print("Notification policy set to 'nobody', exiting")
  sys.exit(0)

if not results:
  print('nothing to report - early-exiting')
  sys.exit(0)

% if github_issue_templates:
github_issue_template_cfgs = [dacite.from_dict(
    data_class=image_scan.GithubIssueTemplateCfg,
    data=raw
    ) for raw in ${[dataclasses.asdict(ghit) for ghit in github_issue_templates]}
]
% endif

max_processing_days = dacite.from_dict(
  data_class=image_scan.MaxProcessingTimesDays,
  data=${dataclasses.asdict(issue_policies.max_processing_time_days)},
)

delivery_svc_endpoints = ccc.delivery.endpoints(cfg_set=cfg_set)
delivery_svc_client = ccc.delivery.default_client_if_available()

% if issue_tgt_repo_url:
gh_api = ccc.github.github_api(repo_url='${issue_tgt_repo_url}')
overwrite_repository = gh_api.repository('${tgt_repo_org}', '${tgt_repo_name}')
% endif

scan_results_vulnerabilities = scan_result_group_collection_for_vulnerabilities(
  results=results,
  cve_threshold=cve_threshold,
)
scan_results_licenses = scan_result_group_collection_for_licenses(
  results=results,
  license_cfg=license_cfg,
)

if not notification_policy is Notify.GITHUB_ISSUES:
  logger.error(f'{notification_policy=} is no longer (or not yet) supported')
  raise NotImplementedError(notification_policy)

for result_group in scan_results_vulnerabilities, scan_results_licenses:
  logger.info(f'processing {result_group.issue_type=}')
  github.compliance.report.create_or_update_github_issues(
    result_group_collection=result_group,
    max_processing_days=max_processing_days,
% if issue_tgt_repo_url:
    gh_api=gh_api,
    overwrite_repository=overwrite_repository,
% endif
% if github_issue_labels_to_preserve:
    preserve_labels_regexes=${github_issue_labels_to_preserve},
% endif
% if github_issue_templates:
    github_issue_template_cfgs=github_issue_template_cfgs,
% endif
    delivery_svc_client=delivery_svc_client,
    delivery_svc_endpoints=delivery_svc_endpoints,
    license_cfg=license_cfg,
  )
</%def>

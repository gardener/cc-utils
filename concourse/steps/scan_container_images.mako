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

filter_cfg = image_scan_trait.matching_config()

license_cfg = image_scan_trait.licenses()

issue_tgt_repo_url = image_scan_trait.overwrite_github_issues_tgt_repository_url()
if issue_tgt_repo_url:
  parsed_repo_url = ci.util.urlparse(issue_tgt_repo_url)
  tgt_repo_org, tgt_repo_name = parsed_repo_url.path.strip('/').split('/')


github_issue_templates = image_scan_trait.github_issue_templates()
github_issue_labels_to_preserve = image_scan_trait.github_issue_labels_to_preserve()

rescoring_rules = image_scan_trait.cve_rescoring_rules()
rescoring_rules_raw = image_scan_trait.cve_rescoring_rules(raw=True)
%>
import logging
import sys

import dacite

# debugging (dump stacktrace on error-signals)
import faulthandler
faulthandler.enable() # print stacktraces upon fatal signals
# end of debugging block

import ccc.aws
import ccc.github
import ccc.oci
import ccc.protecode
import ci.log
ci.log.configure_default_logging()
import ci.util
import concourse.model.traits.image_scan as image_scan
import concourse.model.traits.filter
import delivery.client
import github.compliance.model
import github.compliance.report
import protecode.scanning


from concourse.model.traits.image_scan import Notify
from protecode.model import (
  CVSSVersion,
  ProcessingMode,
)

logger = logging.getLogger('scan_container_images.step')

${step_lib('scan_container_images')}
${step_lib('component_descriptor_util')}

cfg_factory = ci.util.ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

component_descriptor = parse_component_descriptor()

matching_configs = concourse.model.traits.filter.matching_configs_from_dicts(
  dicts=${filter_cfg}
)

filter_function = concourse.model.traits.filter.filter_for_matching_configs(
  configs=matching_configs
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
)

cve_threshold = ${protecode_scan.cve_threshold()}

protecode_client = ccc.protecode.client(protecode_cfg)
delivery_svc_endpoints = ccc.delivery.endpoints(cfg_set=cfg_set)
delivery_svc_client = ccc.delivery.default_client_if_available()

oci_client = ccc.oci.oci_client()
s3_session = ccc.aws.default_session()
if s3_session:
  s3_client =  s3_session.client('s3')
else:
  s3_client = None

logger.info('running protecode scan for all components')
results = tuple(
  protecode.scanning.upload_grouped_images(
    protecode_api=protecode_client,
    protecode_group_id = protecode_group_id,
    component = component_descriptor,
    reference_group_ids = ${protecode_scan.reference_protecode_group_ids()},
    processing_mode = ProcessingMode('${protecode_scan.processing_mode().value}'),
    parallel_jobs=${protecode_scan.parallel_jobs()},
    cve_threshold=cve_threshold,
    filter_function=filter_function,
    delivery_client=delivery_svc_client,
    oci_client=oci_client,
    s3_client=s3_client,
  )
)
logger.info(f'bdba scan yielded {len(results)=}')

% if license_cfg:
license_cfg = dacite.from_dict(
  data_class=image_scan.LicenseCfg,
  data=${dataclasses.asdict(license_cfg)},
)
% else:
license_cfg = None
% endif

% if rescoring_rules:
import dso.cvss
rescoring_rules = tuple(
  dso.cvss.rescoring_rules_from_dicts(
    ${rescoring_rules_raw}
  )
)
% else:
rescoring_rules = None
% endif

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
  data_class=github.compliance.model.MaxProcessingTimesDays,
  data=${dataclasses.asdict(issue_policies.max_processing_time_days)},
)


% if issue_tgt_repo_url:
gh_api = ccc.github.github_api(repo_url='${issue_tgt_repo_url}')
overwrite_repository = gh_api.repository('${tgt_repo_org}', '${tgt_repo_name}')
% endif

scan_results_vulnerabilities = scan_result_group_collection_for_vulnerabilities(
  results=results,
  cve_threshold=cve_threshold,
  rescoring_rules=rescoring_rules,
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
    cfg_set=cfg_set,
  )
</%def>

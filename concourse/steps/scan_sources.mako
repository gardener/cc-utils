<%def
  name="scan_sources_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
import dataclasses
import dacite
import ci.util

main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()

source_scan_trait = job_variant.trait('scan_sources')
checkmarx_cfg = source_scan_trait.checkmarx()
email_recipients = source_scan_trait.email_recipients()
issue_policies = source_scan_trait.issue_policies()
component_trait = job_variant.trait('component_descriptor')
component_descriptor_dir = job_step.input('component_descriptor_dir')
scan_sources_filter = source_scan_trait.filters_raw()
issue_tgt_repo_url = source_scan_trait.overwrite_github_issues_tgt_repository_url()

if issue_tgt_repo_url:
  parsed_repo_url = ci.util.urlparse(issue_tgt_repo_url)
  tgt_repo_org, tgt_repo_name = parsed_repo_url.path.strip('/').split('/')

github_issue_templates = source_scan_trait.github_issue_templates()
github_issue_labels_to_preserve = source_scan_trait.github_issue_labels_to_preserve()

%>

${step_lib('component_descriptor_util')}
${step_lib('scan_sources')}

import sys
import dacite
import ccc.delivery
import ccc.github
import checkmarx.util
import ci.log
ci.log.configure_default_logging()
import ci.util
import delivery.client
import github.compliance.model
import github.compliance.report
from concourse.model.traits.image_scan import (
    GithubIssueTemplateCfg,
    IssuePolicies,
    Notify,
)
cfg_factory = ci.util.ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

component_descriptor = component_descriptor_from_dir(dir_path='${component_descriptor_dir}')

% if github_issue_templates:
github_issue_template_cfgs = [dacite.from_dict(
    data_class=GithubIssueTemplateCfg,
    data=raw
    ) for raw in ${[dataclasses.asdict(ghit) for ghit in github_issue_templates]}
]
% endif

max_processing_days = dacite.from_dict(
  data_class=github.compliance.model.MaxProcessingTimesDays,
  data=${dataclasses.asdict(issue_policies.max_processing_time_days)},
)
severity_threshold = '${checkmarx_cfg.severity_threshold()}'
scan_timeout = ${checkmarx_cfg.scan_timeout()}

delivery_svc_endpoints = ccc.delivery.endpoints(cfg_set=cfg_set)
delivery_svc_client = ccc.delivery.default_client_if_available()

% if issue_tgt_repo_url:
gh_api = ccc.github.github_api(repo_url='${issue_tgt_repo_url}')
overwrite_repository = gh_api.repository('${tgt_repo_org}', '${tgt_repo_name}')
% else:
print('currently, overwrite-repo must be configured!')
exit(1)
% endif

% if checkmarx_cfg:
scan_results = scan_sources(
    checkmarx_cfg_name='${checkmarx_cfg.checkmarx_cfg_name()}',
    component_descriptor=component_descriptor,
    team_id='${checkmarx_cfg.team_id()}',
    threshold=severity_threshold,
    timeout_seconds=scan_timeout,
    include_paths=${checkmarx_cfg.include_path_regexes()},
    exclude_paths=${checkmarx_cfg.exclude_path_regexes()},
)

if not scan_results:
  print('nothing to report - early-exiting')
  sys.exit(0)

# only include results below threshold if email recipients are explicitly configured
notification_policy = Notify('${source_scan_trait.notify().value}')

if notification_policy is Notify.NOBODY:
  print("Notification policy set to 'nobody', exiting")
  sys.exit(0)

if not notification_policy is Notify.GITHUB_ISSUES:
  logger.error(f'{notification_policy=} is no longer (or not yet) supported')
  raise NotImplementedError(notification_policy)

scan_results_grouped = scan_result_group_collection(
  results=scan_results.scans + scan_results.failed_scans,
  severity_threshold=severity_threshold,
)

logger.info('Creating and updating github issues')
github.compliance.report.create_or_update_github_issues(
  result_group_collection=scan_results_grouped,
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
  license_cfg=None,
)
% endif

delivery_service_client = ccc.delivery.default_client_if_available()
if delivery_service_client:
    logger.info('Uploading result to delivery-service')
    for artefact_metadata in checkmarx.util.iter_artefact_metadata(scan_results.scans):
        delivery_service_client.upload_metadata(artefact_metadata)
else:
    logger.warning('Not uploading results to delivery-service, client not available')

</%def>

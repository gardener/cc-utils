<%def name="cfg_reporting_step(step, job, job_mapping, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

extra_args = step._extra_args
cfg_repo_relpath = extra_args['cfg_repo_relpath']
raw_job_mapping = extra_args['raw_job_mapping']
job_mapping_name = extra_args['job_mapping_name']
compliance_reporting_repo_url = extra_args['compliance_reporting_repo_url']
delivery_endpoints_cfg_name = extra_args['delivery_endpoints_cfg_name']
github_issue_template_cfgs_raw = extra_args['github_issue_template_cfgs_raw']
cfg_repo_url = extra_args['cfg_repo_url']
%>

${step_lib('cfg_reporting')}
import dacite

import ccc.delivery
import ccc.github
import cfg_mgmt.reporting as cmr
import ci.util
import concourse.model.traits.image_scan
import github.compliance.model as gcm
import github.compliance.report as gcr
import model
import model.concourse


cfg_dir = '${cfg_repo_relpath}'
cfg_factory: model.ConfigFactory = model.ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir)
org_job_mapping = model.concourse.JobMapping(name='${job_mapping_name}', raw_dict=${raw_job_mapping})
cfg_set = cfg_factory.cfg_set(org_job_mapping.replication_ctx_cfg_set())

status_reports = cmr.generate_cfg_element_status_reports(
  cfg_dir='${cfg_repo_relpath}',
  element_storage='${cfg_repo_url}',
)

gh_api = ccc.github.github_api(
  repo_url='${compliance_reporting_repo_url}',
  cfg_factory=cfg_factory,
)
parsed_repo_url = ci.util.urlparse('${compliance_reporting_repo_url}')
org, repo_name = parsed_repo_url.path.strip('/').split('/')
repository = gh_api.repository(owner=org, repository=repo_name)

results = [
  gcm.CfgScanResult(
    evaluation_result=cmr.evaluate_cfg_element_status(status_report),
    scanned_element=status_report,
  )
  for status_report in status_reports
]

delivery_svc_client = ccc.delivery.client(
  cfg_name='${delivery_endpoints_cfg_name}',
  cfg_factory=cfg_factory,
)

grouped_no_status = scan_result_group_collection_for_no_status(results)
grouped_no_responsible = scan_result_group_collection_for_no_responsible(results)
grouped_no_rule = scan_result_group_collection_for_no_rule(results)
grouped_no_outdated = scan_result_group_collection_for_outdated(results)
grouped_no_undefined_policy = scan_result_group_collection_for_undefined_policy(results)

github_issue_template_cfgs = [
  dacite.from_dict(
    data_class=concourse.model.traits.image_scan.GithubIssueTemplateCfg,
    data=template_cfg_raw,
  )
  for template_cfg_raw in ${github_issue_template_cfgs_raw}
]

for result_group_collection in (
  grouped_no_status,
  grouped_no_responsible,
  grouped_no_rule,
  grouped_no_outdated,
  grouped_no_undefined_policy,
):
  gcr.create_or_update_github_issues(
    result_group_collection=result_group_collection,
    max_processing_days=gcm.MaxProcessingTimesDays(),
    gh_api=gh_api,
    overwrite_repository=repository,
    delivery_svc_client=delivery_svc_client,
    github_issue_template_cfgs=github_issue_template_cfgs,
  )

</%def>

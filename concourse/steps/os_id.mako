<%def
  name="os_id_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
import concourse.steps
import ci.util
import dataclasses

image_scan_trait = job_variant.trait('image_scan')
issue_policies = image_scan_trait.issue_policies()
os_id = image_scan_trait.os_id()
component_trait = job_variant.trait('component_descriptor')
root_component_name = component_trait.component_name()

delivery_svc_cfg_name = cfg_set.delivery_endpoints().name()

issue_tgt_repo_url = image_scan_trait.overwrite_github_issues_tgt_repository_url()
if issue_tgt_repo_url:
  parsed_repo_url = ci.util.urlparse(issue_tgt_repo_url)
  tgt_repo_org, tgt_repo_name = parsed_repo_url.path.strip('/').split('/')

github_issue_labels_to_preserve = image_scan_trait.github_issue_labels_to_preserve()
github_issue_templates = image_scan_trait.github_issue_templates()
%>
import dacite

import ccc.delivery
import ccc.github
import ccc.oci
import ci.util
import concourse.model.traits.image_scan as image_scan
import cnudie.retrieve
import github.compliance.model
import github.compliance.report

${concourse.steps.step_lib('os_id')}
${concourse.steps.step_lib('component_descriptor_util')}

component_descriptor = parse_component_descriptor()
delivery_db_client = ccc.delivery.default_client_if_available()
oci_client = ccc.oci.oci_client()

max_processing_days = dacite.from_dict(
  data_class=github.compliance.model.MaxProcessingTimesDays,
  data=${dataclasses.asdict(issue_policies.max_processing_time_days)},
)

cfg_factory = ci.util.ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")
delivery_svc_endpoints = ccc.delivery.endpoints(cfg_set=cfg_set)

if not '${delivery_svc_cfg_name}':
  logger.error('no deliverydb-client available - exiting now')
  exit(1)

% if issue_tgt_repo_url:
gh_api = ccc.github.github_api(repo_url='${issue_tgt_repo_url}')
overwrite_repository = gh_api.repository('${tgt_repo_org}', '${tgt_repo_name}')
% else:
gh_api = None
overwrite_repository = None
% endif

% if github_issue_templates:
github_issue_template_cfgs = [dacite.from_dict(
    data_class=image_scan.GithubIssueTemplateCfg,
    data=raw
    ) for raw in ${[dataclasses.asdict(ghit) for ghit in github_issue_templates]}
]
% endif

results = []

for result in determine_os_ids(
  component_descriptor=component_descriptor,
  oci_client=oci_client,
):
  component = result.scanned_element.component
  resource = github.compliance.model.artifact_from_node(result.scanned_element)
  os_info = result.os_id

  logger.info(f'uploading os-info for {component.name} {resource.name}')
  upload_to_delivery_db(
    db_client=delivery_db_client,
        resource=resource,
        component=component,
        os_info=os_info,
  )
  results.append(result)

logger.info(f'uploaded {len(results)=} os-infos')

result_group_collection = scan_result_group_collection_for_outdated_os_ids(
  results=results,
  delivery_svc_client=delivery_db_client,
)

github.compliance.report.create_or_update_github_issues(
  result_group_collection=result_group_collection,
  max_processing_days=max_processing_days,
  gh_api=gh_api,
  overwrite_repository=overwrite_repository,
% if github_issue_labels_to_preserve:
    preserve_labels_regexes=${github_issue_labels_to_preserve},
% endif
% if github_issue_templates:
    github_issue_template_cfgs=github_issue_template_cfgs,
% endif
  delivery_svc_client=delivery_db_client,
  delivery_svc_endpoints=delivery_svc_endpoints,
  license_cfg=None,
)

</%def>

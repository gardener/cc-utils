<%def name="render_repositories(pipeline_definition, cfg_set)">
<%
from concourse.client.model import ResourceType
%>
<%namespace file="/resources/github.mako" import="*"/>
% for repo_cfg in pipeline_definition._resource_registry.resources(type_name=ResourceType.GIT.value):
<%
if repo_cfg.cfg_name() and repo_cfg.cfg_name() != cfg_set.github().name():
  # hack: If github is not default github webhook assume webhook delivery is not possible
  # (e.g. sap-internal GitHub <-> sap-external Concourse)
  configure_webhook = False
else:
  configure_webhook = True
%>
${github_repo(
  repo_cfg=repo_cfg,
  cfg_set=cfg_set,
  configure_webhook=configure_webhook,
)}
% endfor
% for repo_cfg in pipeline_definition._resource_registry.resources(type_name=ResourceType.PULL_REQUEST.value):
<%
require_label = None
# if we have at least one pr-repo, there must be a pr-trait
for variant in pipeline_definition.variants():
  if variant.has_trait('pull-request'):
    require_label = variant.trait('pull-request').policies().require_label()
  concourse_cfg = cfg_set.concourse()
  disable_github_pr_webhooks = concourse_cfg.disable_github_pr_webhooks()
%>
${github_pr(
  repo_cfg=repo_cfg,
  cfg_set=cfg_set,
  require_label=require_label,
  configure_webhook=not disable_github_pr_webhooks,
)}
% endfor
</%def>

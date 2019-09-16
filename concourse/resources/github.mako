<%def name="git_ignore_paths(repo_cfg)">
  % if repo_cfg.trigger_include_paths():
    paths: ${repo_cfg.trigger_include_paths()}
  % endif
  % if repo_cfg.trigger_exclude_paths():
    ignore_paths: ${repo_cfg.trigger_exclude_paths()}
  % endif
</%def>
<%def name="_common_github_resource_config(repo_cfg, github_cfg)">
<%
from makoutil import indent_func
from model.github import Protocol
disable_tls_validation = github_cfg.tls_validation()
credentials = github_cfg.credentials()
%>
    skip_ssl_verification: ${disable_tls_validation}
    no_ssl_verify: ${disable_tls_validation}
% if github_cfg.preferred_protocol() is Protocol.SSH:
    uri: ${github_cfg.ssh_url()}/${repo_cfg.repo_path()}
    private_key: |
      ${indent_func(6)(credentials.private_key()).strip()}
% elif github_cfg.preferred_protocol() is Protocol.HTTPS:
    uri: ${github_cfg.http_url()}/${repo_cfg.repo_path()}
    username: "${credentials.username()}"
    password: "${credentials.passwd()}"
% else:
  <% raise NotImplementedException %>
% endif
${git_ignore_paths(repo_cfg)}
</%def>
<%def name="github_repo(repo_cfg, cfg_set, configure_webhook=True)">
<%
repo_name = repo_cfg.name()
if repo_name is not None:
  github_cfg = cfg_set.github(cfg_name=repo_cfg.cfg_name())
else:
  github_cfg = cfg_set.github()
%>
- name: ${repo_cfg.resource_name()}
% if configure_webhook:
  <<: *configure_webhook
% endif
  type: git
  source:
    branch: ${repo_cfg.branch()}
    disable_ci_skip: ${repo_cfg.disable_ci_skip()}
${_common_github_resource_config(repo_cfg, github_cfg)}
</%def>
<%def name="github_pr(repo_cfg, cfg_set, require_label=None, configure_webhook=True)">
<%
repo_name = repo_cfg.name()
if repo_name is not None:
  github_cfg = cfg_set.github(cfg_name=repo_cfg.cfg_name())
else:
  github_cfg = cfg_set.github()
credentials = github_cfg.credentials()
%>
- name: ${repo_cfg.resource_name()}
% if configure_webhook:
  <<: *configure_webhook
% endif
  type: pull-request
  source:
    repo: ${repo_cfg.repo_path()}
    base: ${repo_cfg.branch()}
    api_endpoint: ${github_cfg.api_url()}
    access_token: ${credentials.auth_token()}
% if require_label:
    label: "${require_label}"
% endif
${_common_github_resource_config(repo_cfg, github_cfg)}
</%def>

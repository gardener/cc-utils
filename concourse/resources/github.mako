<%def name="git_ignore_paths(repo_cfg)">
  % if repo_cfg.trigger_include_paths():
    paths: ${repo_cfg.trigger_include_paths()}
  % endif
  % if repo_cfg.trigger_exclude_paths():
    ignore_paths: ${repo_cfg.trigger_exclude_paths()}
  % endif
</%def>
<%def name="github_repo(repo_cfg, cfg_set, configure_webhook=True)">
<%
from makoutil import indent_func
from model.github import Protocol
repo_name = repo_cfg.name()
if repo_name is not None:
  github_cfg = cfg_set.github(cfg_name=repo_cfg.cfg_name())
else:
  github_cfg = cfg_set.github()
credentials = github_cfg.credentials()
disable_tls_validation = not github_cfg.tls_validation()
%>
- name: ${repo_cfg.resource_name()}
% if configure_webhook:
  <<: *configure_webhook
% endif
  type: git
  source:
% if github_cfg.preferred_protocol() is Protocol.SSH:
    uri: ${github_cfg.ssh_url()}/${repo_cfg.repo_path()}
% elif github_cfg.preferred_protocol() is Protocol.HTTPS:
    uri: ${github_cfg.http_url()}/${repo_cfg.repo_path()}
% else:
  <% raise NotImplementedException %>
% endif
    branch: ${repo_cfg.branch()}
    disable_ci_skip: ${repo_cfg.disable_ci_skip()}
    skip_ssl_verification: ${disable_tls_validation}
    no_ssl_verify: ${disable_tls_validation}
    private_key: |
      ${indent_func(6)(credentials.private_key()).strip()}
    username: "${credentials.username()}"
    password: "${credentials.passwd()}"
${git_ignore_paths(repo_cfg)}
</%def>
<%def name="github_pr(repo_cfg, cfg_set, require_label=None, configure_webhook=True)">
<%
from makoutil import indent_func
from model.github import Protocol
repo_name = repo_cfg.name()
if repo_name is not None:
  github_cfg = cfg_set.github(cfg_name=repo_cfg.cfg_name())
else:
  github_cfg = cfg_set.github()

disable_tls_validation = github_cfg.tls_validation()
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
% if github_cfg.preferred_protocol() is Protocol.SSH:
    uri: ${github_cfg.ssh_url()}/${repo_cfg.repo_path()}
% elif github_cfg.preferred_protocol() is Protocol.HTTPS:
    uri: ${github_cfg.http_url()}/${repo_cfg.repo_path()}
% else:
  <% raise NotImplementedException %>
% endif
    api_endpoint: ${github_cfg.api_url()}
    skip_ssl_verification: ${disable_tls_validation}
    access_token: ${credentials.auth_token()}
    no_ssl_verify: ${disable_tls_validation}
    private_key: |
      ${indent_func(6)(credentials.private_key()).strip()}
    username: "${credentials.username()}"
    password: "${credentials.passwd()}"
% if require_label:
    label: "${require_label}"
% endif
${git_ignore_paths(repo_cfg)}
</%def>

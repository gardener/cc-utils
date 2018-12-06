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
    uri: ${github_cfg.ssh_url()}/${repo_cfg.repo_path()}
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
github_cfg = cfg_set.github(cfg_name=repo_cfg.cfg_name())
%>
- name: ${repo_cfg.resource_name()}
% if configure_webhook:
  <<: *configure_webhook
% endif
  type: pull-request
  source:
    <<: *pull_request_defaults
    repo: ${repo_cfg.repo_path()}
    base: ${repo_cfg.branch()}
    uri: ${github_cfg.ssh_url()}/${repo_cfg.repo_path()}
% if require_label:
    label: "${require_label}"
% endif
${git_ignore_paths(repo_cfg)}
</%def>

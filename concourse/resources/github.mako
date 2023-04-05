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
import ccc.github
import ci.util
from concourse.client.model import ResourceType
from makoutil import indent_func
from model.github import Protocol

if github_cfg_name := repo_cfg.cfg_name():
  github_cfg = cfg_set.github(cfg_name=github_cfg_name)
elif github_cfg := ccc.github.github_cfg_for_repo_url(
    ci.util.urljoin(
      repo_cfg.repo_hostname(),
      repo_cfg.repo_path(),
    ),
    cfg_factory=cfg_set,
    require_labels=('ci',),
  ):
  pass
else:
  raise RuntimeError(f'this line should not have been reached. Function before should have raised')

submodule_cfg_names = set(repo_cfg.submodule_cfg_names())
submodule_cfg_names.add(github_cfg.name())
submodule_cfgs = [
  cfg_set.github(cfg_name=github_cfg_name)
  for github_cfg_name in submodule_cfg_names
]

credentials = github_cfg.credentials()
disable_tls_validation = not github_cfg.tls_validation()
token_or_passwd = credentials.auth_token() or credentials.passwd()

preferred_protocol = github_cfg.preferred_protocol()
# repo-specific cfg "wins"
if (overwrite_preferred_protocol := repo_cfg.preferred_protocol()):
  preferred_protocol = overwrite_preferred_protocol
%>
- name: ${repo_cfg.resource_name()}
% if configure_webhook:
  <<: *configure_webhook
% endif
  type: ${ResourceType.GIT.value}
  source:
% if preferred_protocol is Protocol.SSH:
    uri: ${github_cfg.ssh_url()}/${repo_cfg.repo_path()}
% elif preferred_protocol is Protocol.HTTPS:
    uri: ${github_cfg.http_url()}/${repo_cfg.repo_path()}
% else:
  <% raise NotImplementedError %>
% endif
    branch: ${repo_cfg.branch()}
    disable_ci_skip: ${repo_cfg.disable_ci_skip()}
    skip_ssl_verification: ${disable_tls_validation}
    no_ssl_verify: ${disable_tls_validation}
    private_key: |
      ${indent_func(6)(credentials.private_key()).strip()}
    username: '${credentials.username()}'
    password: '${token_or_passwd}'
    git_config:
    - name: 'protocol.version'
      value: '2'
  % if submodule_cfgs:
    submodule_credentials:
    % for submodule_cfg in submodule_cfgs:
      <%
      submodule_credentials = submodule_cfg.credentials()
      submodule_token_or_passwd = submodule_credentials.auth_token() or submodule_credentials.passwd()
      %>
      % if Protocol.HTTPS in submodule_cfg.available_protocols():
    - host: '${submodule_cfg.hostname()}'
      username: '${submodule_credentials.username()}'
      password: '${submodule_token_or_passwd}'
      % endif
    % endfor

% endif
${git_ignore_paths(repo_cfg)}
</%def>
<%def name="github_pr(repo_cfg, cfg_set, require_label=None, configure_webhook=True)">
<%
from concourse.client.model import ResourceType
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
  type: ${ResourceType.PULL_REQUEST.value}
  source:
    repo: ${repo_cfg.repo_path()}
    base: ${repo_cfg.branch()}
% if github_cfg.preferred_protocol() is Protocol.SSH:
    uri: ${github_cfg.ssh_url()}/${repo_cfg.repo_path()}
% elif github_cfg.preferred_protocol() is Protocol.HTTPS:
    uri: ${github_cfg.http_url()}/${repo_cfg.repo_path()}
% else:
  <% raise NotImplementedError %>
% endif
    api_endpoint: ${github_cfg.api_url()}
    skip_ssl_verification: ${disable_tls_validation}
    access_token: ${credentials.auth_token()}
    no_ssl_verify: ${disable_tls_validation}
    private_key: |
      ${indent_func(6)(credentials.private_key()).strip()}
    username: '${credentials.username()}'
    password: '${credentials.passwd()}'
% if require_label:
    label: '${require_label}'
% endif
${git_ignore_paths(repo_cfg)}
</%def>

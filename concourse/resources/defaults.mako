<%namespace file="/resources/image.mako" import="task_image_resource"/>
<%def name='task_image_defaults(registry_cfg, indent=0)'
filter="indent_func(indent),trim">
<%
from makoutil import indent_func
# registry_cfg must be of type ContainerRegistryConfig (cc-utils)
tag = "1.337.0"
repository = "eu.gcr.io/gardener-project/cc/job-image"
%>
${task_image_resource(
  registry_cfg,
  image_repository=repository,
  image_tag=tag,
  indent=0,
)}
</%def>
<%def name='configure_webhook(webhook_token)'>
  configure_webhook: &configure_webhook
    webhook_token: ${webhook_token}
    check_every: 12h
</%def>
<%def name="pull_request_defaults(github_cfg)">
<%
from makoutil import indent_func
disable_tls_validation = 'false' if github_cfg.tls_validation() else 'true'
credentials = github_cfg.credentials()
%>
  pull_request_defaults: &pull_request_defaults
    skip_ssl_verification: ${disable_tls_validation}
    no_ssl_verify: ${disable_tls_validation}
    private_key: |
      ${indent_func(6)(credentials.private_key()).strip()}
    access_token: ${credentials.auth_token()}
    api_endpoint: ${github_cfg.api_url()}
</%def>

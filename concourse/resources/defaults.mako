<%def name='task_image_defaults(registry_cfg, indent=0)'
filter="indent_func(indent),trim">
<%
from makoutil import indent_func
# registry_cfg must be of type ContainerRegistryConfig (cc-utils)
credentials = registry_cfg.credentials()
%>
    platform: linux
    image_resource:
      type: docker-image
      source:
        username: '${credentials.username()}'
        password: '${credentials.passwd()}'
        repository: eu.gcr.io/gardener-project/cc/job-image
        tag: "1.44.0"
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

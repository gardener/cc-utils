<%def name='image_registry_defaults(registry_name, registry_cfg)'>
<%
# registry_cfg must be of type ContainerRegistryConfig (cc-utils)
credentials = registry_cfg.credentials()
%>
  ${registry_name}_defaults: &${registry_name}_defaults
    username: '${credentials.username()}'
    password: '${credentials.passwd()}'
</%def>
<%def name='task_image_resource(registry_name)'>
  task_image_resource: &task_image_resource
    platform: linux
    image_resource:
      type: docker-image
      source:
        <<: *${registry_name}_defaults
        repository: eu.gcr.io/gardener-project/cc/job-image
        tag: "1.48.0"
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

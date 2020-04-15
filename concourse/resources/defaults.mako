<%namespace file="/resources/image.mako" import="task_image_resource"/>
<%def name='task_image_defaults(registry_cfg, indent=0)'
filter="indent_func(indent),trim">
<%
from makoutil import indent_func
# registry_cfg must be of type ContainerRegistryConfig (cc-utils)
tag = "1.607.0"
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
    check_every: 4h
</%def>

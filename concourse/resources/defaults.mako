<%namespace file="/resources/image.mako" import="task_image_resource"/>
<%def name='task_image_defaults(registry_cfg, indent=0)'
filter="indent_func(indent),trim">
<%
import os
from makoutil import indent_func
import model.container_registry as mcr
import concourse.paths

if not (job_image_tag := os.environ.get('CC_JOB_IMAGE_TAG', '')):
  with open(concourse.paths.last_released_tag_file) as f:
    job_image_tag = f.read().strip()

# registry_cfg must be of type ContainerRegistryConfig (cc-utils)
repository = 'eu.gcr.io/gardener-project/cc/job-image'
registry_cfg = mcr.find_config(image_reference=repository)
%>
${task_image_resource(
  registry_cfg,
  image_repository=repository,
  image_tag=job_image_tag,
  indent=0,
)}
</%def>
<%def name='configure_webhook(webhook_token)'>
  configure_webhook: &configure_webhook
    webhook_token: ${webhook_token}
    check_every: 4h
</%def>

<%namespace file="/resources/image.mako" import="task_image_resource"/>
<%def name='task_image_defaults(registry_cfg, platform=None, indent=0)'
filter="indent_func(indent),trim">
<%
# platform: model.concourse.Platform
import os
from makoutil import indent_func
import model.container_registry as mcr
import concourse.paths

if not (job_image_tag := os.environ.get('CC_JOB_IMAGE_TAG', '')):
  with open(concourse.paths.last_released_tag_file) as f:
    job_image_tag = f.read().strip()

if platform:
    job_image_tag = f'{job_image_tag}-{platform.normalised_oci_platform_tag_suffix}'

# registry_cfg must be of type ContainerRegistryConfig (cc-utils)
repository = 'europe-docker.pkg.dev/gardener-project/releases/cicd/job-image'
registry_cfg = mcr.find_config(image_reference=repository)
%>
${task_image_resource(
  registry_cfg,
  image_repository=repository,
  image_tag=job_image_tag,
  indent=0,
)}
</%def>

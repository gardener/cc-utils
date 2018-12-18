<%def name="container_registry_image_resource(name, image_reference, registry_cfg)">
<%
# registry_cfg should be of type ContainerRegistryConfig (from cc-utils)
credentials = registry_cfg.credentials()
%>
- name: ${name}
  type: docker-image
  source:
    username: '${credentials.username()}'
    password: '${credentials.passwd()}'
    repository: ${image_reference}
</%def>
<%def name="task_image_resource(registry_cfg, image_repository, image_tag, indent=0)"
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
    repository: '${image_repository}'
    tag: '${image_tag}'
</%def>

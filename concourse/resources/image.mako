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

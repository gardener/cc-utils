<%def name="task_image_resource(registry_cfg, image_repository, image_tag, indent=0)"
filter="indent_func(indent),trim">
<%
from makoutil import indent_func
%>
platform: linux
image_resource:
  type: docker-image
  source:
% if registry_cfg is not None:
<%
credentials = registry_cfg.credentials()
%>
    username: '${credentials.username()}'
    password: '${credentials.passwd()}'
% endif
    repository: '${image_repository}'
    tag: '${image_tag}'
</%def>

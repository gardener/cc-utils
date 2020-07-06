<%def name="include_pull_request_resource_type()">
<%
from concourse.client.model import ResourceType
%>
- name: ${ResourceType.PULL_REQUEST.value}
  type: docker-image
  source:
    repository: jtarchie/pr
</%def>

<%def name="include_email_resource_type()">
- name: email
  type: docker-image
  source:
    repository: pcfseceng/email-resource
</%def>
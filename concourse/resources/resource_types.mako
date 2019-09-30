<%def name="include_pull_request_resource_type()">
- name: pull-request
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
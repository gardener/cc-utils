<%def name="include_pull_request_resource_type()">
<%
from concourse.client.model import ResourceType
%>
- name: ${ResourceType.PULL_REQUEST.value}
  type: registry-image
  source:
    repository: eu.gcr.io/gardener-project/cc/pr-resource
</%def>

<%def name="include_git_resource_type()">
- name: 'git'
  type: 'registry-image'
  source:
    repository: eu.gcr.io/gardener-project/cc/git-resource
    tag: latest
</%def>

<%def name="include_time_resource_type()">
- name: 'time'
  type: 'registry-image'
  source:
    repository: eu.gcr.io/gardener-project/cc/time-resource
    tag: latest
</%def>

<%def name="meta_resource(pipeline_definition)">
% for meta_res in pipeline_definition._resource_registry.resources(type_name='meta'):
- name: ${meta_res.resource_identifier().name()}
  check_every: 240h
  type: meta
% endfor
</%def>

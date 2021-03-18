<%def name="replicate_pipelines_step(step, job, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

extra_args = step._extra_args

concourse_cfg = extra_args['concourse_cfg']
job_mappings = extra_args['job_mappings']

%>

${step_lib('replicate_pipelines')}

</%def>

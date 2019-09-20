<%def name="meta_step(job_step, job_variant, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

%>

${step_lib('meta')}

export_job_metadata()

</%def>

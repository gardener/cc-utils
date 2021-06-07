<%def name="replicate_pipelines_step(step, job, job_mapping, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

extra_args = step._extra_args

cfg_set = extra_args['cfg_set']

name = job_mapping.name()
raw = job_mapping.raw
%>
import ci.util
import ctx
import model.concourse

${step_lib('replicate_pipelines')}

cfg_factory = ctx.cfg_factory()
cfg_set = cfg_factory.cfg_set('${cfg_set.name()}')

own_pipeline_name = ci.util.check_env('PIPELINE_NAME')
job_mapping = model.concourse.JobMapping(name='${name}', raw_dict=${raw})

## use logger from step_lib
logger.info(f'replicating {job_mapping.name()=} {job_mapping.team_name()=}')

replicate_pipelines(
  cfg_set=cfg_set,
  job_mapping=job_mapping,
  own_pipeline_name=own_pipeline_name,
)

</%def>

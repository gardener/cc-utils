<%def name="replicate_secrets_step(step, job, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

extra_args = step._extra_args

cfg_set = extra_args['cfg_set']
job_mapping = extra_args['job_mapping']
job_mapping_set = extra_args['job_mapping_set']
concourse_cfg = cfg_set.concourse()
%>

import ci.util
import ctx

${step_lib('replicate_secrets')}

cfg_factory = ctx.cfg_factory()

cfg_set = cfg_factory.cfg_set('${cfg_set.name()}')
concourse_cfg = cfg_factory.concourse('${concourse_cfg.name()}')
job_mapping_set = cfg_factory.job_mapping('${job_mapping_set.name()}')
job_mapping = job_mapping_set['${job_mapping.name()}']
own_pipeline_name = ci.util.check_env('PIPELINE_NAME')

## use logger from step_lib
logger.info(f'replicating secret for {job_mapping.name()=} {job_mapping.team_name()=}')

replicate_secrets()

</%def>

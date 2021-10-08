<%def name="replicate_pipelines_step(step, job, job_mapping, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

extra_args = step._extra_args
cc_utils_version = extra_args.get('cc_utils_version', '<unknown>') # remove fallback
pipelines_not_to_delete = extra_args.get('pipelines_not_to_delete')

name = job_mapping.name()
raw = job_mapping.raw
%>
import ci.util
import ctx
import model.concourse

${step_lib('replicate_pipelines')}

job_mapping = model.concourse.JobMapping(name='${name}', raw_dict=${raw})

cfg_factory = ctx.cfg_factory()
cfg_set = cfg_factory.cfg_set('${job_mapping.replication_ctx_cfg_set()}')

own_pipeline_name = ci.util.check_env('PIPELINE_NAME')
cc_utils_version = '${cc_utils_version}'

pipelines_not_to_delete = list(${pipelines_not_to_delete})
pipelines_not_to_delete.append(own_pipeline_name)

## use logger from step_lib
logger.info(f'replicating {job_mapping.name()=} {job_mapping.team_name()=} {cc_utils_version=}')

replicate_pipelines(
  cfg_set=cfg_set,
  job_mapping=job_mapping,
  pipelines_not_to_delete=pipelines_not_to_delete,
)

</%def>

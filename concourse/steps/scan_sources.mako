<%def
  name="scan_sources_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()

source_scan_trait = job_variant.trait('scan_sources')
checkmarx_cfg = source_scan_trait.checkmarx()
component_trait = job_variant.trait('component_descriptor')

%>
${step_lib('component_descriptor_util')}
${step_lib('scan_sources')}
scan_sources(
    checkmarx_cfg_name='${checkmarx_cfg.checkmarx_cfg_name()}',
    team_id='${checkmarx_cfg.team_id()}',
    component_descriptor=component_descriptor_path(),
)

</%def>

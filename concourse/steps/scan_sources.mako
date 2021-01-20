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
whitesource_cfg = source_scan_trait.whitesource()
email_recipients = source_scan_trait.email_recipients()
component_trait = job_variant.trait('component_descriptor')

%>
${step_lib('component_descriptor_util')}
${step_lib('scan_sources')}

import gci.componentmodel as cm

% if checkmarx_cfg:
scan_sources_and_notify(
    checkmarx_cfg_name='${checkmarx_cfg.checkmarx_cfg_name()}',
    component_descriptor_path=component_descriptor_path(schema_version=cm.SchemaVersion.V2),
    email_recipients=${email_recipients},
    team_id='${checkmarx_cfg.team_id()}',
    threshold=${checkmarx_cfg.severity_threshold()},
    include_paths=${checkmarx_cfg.include_path_regexes()},
    exclude_paths=${checkmarx_cfg.exclude_path_regexes()},
)
% endif

% if whitesource_cfg:
component_name = '${component_trait.component_name()}'
scan_component_with_whitesource(
    component_descriptor_path=component_descriptor_path(schema_version=cm.SchemaVersion.V2),
    cve_threshold=${whitesource_cfg.cve_threshold()},
    extra_whitesource_config={},
    notification_recipients=${email_recipients},
    whitesource_cfg_name='${whitesource_cfg.cfg_name()}',
)
% endif

</%def>

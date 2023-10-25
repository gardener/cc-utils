<%def
  name="scan_container_images_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
import dataclasses

image_scan_trait = job_variant.trait('image_scan')
protecode_scan = image_scan_trait.protecode()
auto_assess_max_severity = protecode_scan.auto_assess_max_severity.name

filter_cfg = image_scan_trait.matching_config()

license_cfg = image_scan_trait.licenses()

rescoring_rules = image_scan_trait.cve_rescoring_rules()
rescoring_rules_raw = image_scan_trait.cve_rescoring_rules(raw=True)
%>
import logging
import sys

import dacite

# debugging (dump stacktrace on error-signals)
import faulthandler
faulthandler.enable() # print stacktraces upon fatal signals
# end of debugging block

import ccc.aws
import ccc.delivery
import ccc.oci
import ccc.protecode
import ci.log
ci.log.configure_default_logging()
import ci.util
import concourse.model.traits.image_scan as image_scan
import concourse.model.traits.filter
import delivery.client
import protecode.scanning
import protecode.rescore
import protecode.util


from concourse.model.traits.image_scan import Notify
from protecode.model import (
  VulnerabilityScanResult,
  LicenseScanResult,
  ComponentsScanResult,
  CVSSVersion,
  ProcessingMode,
)

logger = logging.getLogger('scan_container_images.step')

${step_lib('scan_container_images')}
${step_lib('component_descriptor_util')}

cfg_factory = ci.util.ctx().cfg_factory()

component_descriptor = parse_component_descriptor()

matching_configs = concourse.model.traits.filter.matching_configs_from_dicts(
  dicts=${filter_cfg}
)

filter_function = concourse.model.traits.filter.filter_for_matching_configs(
  configs=matching_configs
)

% if not protecode_scan.protecode_cfg_name():
protecode_cfg = cfg_factory.protecode()
% else:
protecode_cfg = cfg_factory.protecode('${protecode_scan.protecode_cfg_name()}')
% endif

protecode_group_id = ${protecode_scan.protecode_group_id()}
protecode_group_url = f'{protecode_cfg.api_url()}/group/{protecode_group_id}/'

print_protecode_info_table(
  protecode_group_id = protecode_group_id,
  reference_protecode_group_ids = ${protecode_scan.reference_protecode_group_ids()},
  protecode_group_url = protecode_group_url,
  cvss_version = CVSSVersion('${protecode_scan.cvss_version().value}'),
)

cve_threshold = ${protecode_scan.cve_threshold()}

protecode_client = ccc.protecode.client(protecode_cfg=protecode_cfg)
delivery_svc_client = ccc.delivery.default_client_if_available()

oci_client = ccc.oci.oci_client()
s3_session = ccc.aws.default_session()
if s3_session:
  s3_client =  s3_session.client('s3')
else:
  s3_client = None

% if license_cfg:
license_cfg = dacite.from_dict(
  data_class=image_scan.LicenseCfg,
  data=${dataclasses.asdict(license_cfg)},
)
% else:
license_cfg = None
% endif

logger.info('running protecode scan for all components')
results = tuple(
  protecode.scanning.upload_grouped_images(
    protecode_api=protecode_client,
    bdba_cfg_name=protecode_cfg.name(),
    protecode_group_id = protecode_group_id,
    component = component_descriptor,
    reference_group_ids = ${protecode_scan.reference_protecode_group_ids()},
    processing_mode = ProcessingMode('${protecode_scan.processing_mode().value}'),
    parallel_jobs=${protecode_scan.parallel_jobs()},
    cve_threshold=cve_threshold,
    filter_function=filter_function,
    delivery_client=delivery_svc_client,
    oci_client=oci_client,
    s3_client=s3_client,
    license_cfg=license_cfg,
  )
)

vulnerability_results = []
license_results = []
components_results = []
for result in results:
  if type(result) is VulnerabilityScanResult:
    vulnerability_results.append(result)
  elif type(result) is LicenseScanResult:
    license_results.append(result)
  elif type(result) is ComponentsScanResult:
    components_results.append(result)
  else:
    raise NotImplementedError(f'result with {type(result)=} not supported')

logger.info(f'bdba scan yielded {len(results)=}')
logger.info(f'- {len(vulnerability_results)} vulnerability results')
logger.info(f'- {len(license_results)} license results')
logger.info(f'- {len(components_results)} component results')

% if rescoring_rules:
import dso.cvss
rescoring_rules = tuple(
  dso.cvss.rescoring_rules_from_dicts(
    ${rescoring_rules_raw}
  )
)
% else:
rescoring_rules = None
% endif

if not results:
  print('nothing to report - early-exiting')
  sys.exit(0)

% if rescoring_rules:
## rescorings
for components_result in components_results:
  rescored_vulnerability_results = protecode.rescore.rescore(
    bdba_client=protecode_client,
    components_scan_result=components_result,
    vulnerability_scan_results=vulnerability_results,
    rescoring_rules=rescoring_rules,
    max_rescore_severity=dso.cvss.CVESeverity['${auto_assess_max_severity}'],
  )
  if rescored_vulnerability_results:
    logger.info('sync rescored vulnerability results with delivery-db')
    protecode.util.sync_results_with_delivery_db(
      delivery_client=delivery_svc_client,
      results=rescored_vulnerability_results,
      bdba_cfg_name=protecode_cfg.name(),
    )
% endif
</%def>

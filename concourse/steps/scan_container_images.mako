<%def
  name="scan_container_images_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()

image_scan_trait = job_variant.trait('image_scan')
upload_registry_prefix = image_scan_trait.upload_registry_prefix()
filter_cfg = image_scan_trait.filters()
component_trait = job_variant.trait('component_descriptor')
%>
import sys
import pathlib
import textwrap
import tabulate

import mailutil
import product.model
import product.util
import protecode.util
import util

from product.scanning import ProcessingMode

util.ctx().configure_default_logging()

${step_lib('scan_container_images')}

# XXX suppress warnings for sap-ca
# (is installed in truststore in cc-job-image, but apparently not honoured by httlib2)
import urllib3
urllib3.disable_warnings()

cfg_factory = util.ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")


% if not image_scan_trait.protecode_cfg_name():
protecode_cfg = cfg_factory.protecode()
% else:
protecode_cfg = cfg_factory.protecode('${image_scan_trait.protecode_cfg_name()}')
% endif

protecode_group_id = int(${image_scan_trait.protecode_group_id()})
protecode_group_url = f'{protecode_cfg.api_url()}/group/{protecode_group_id}/'

# print configuration
print(tabulate.tabulate(
  (
    ('Protecode target group id', str(protecode_group_id)),
    ('Protecode group URL', protecode_group_url),
    ('Protecode reference group IDs', ${image_scan_trait.reference_protecode_group_ids()}),
    ('Image Filter (include)', ${filter_cfg.include_image_references()}),
    ('Image Filter (exclude)', ${filter_cfg.exclude_image_references()}),
% if upload_registry_prefix:
    ('Upload Registry prefix', '${upload_registry_prefix}'),
% endif
  ),
))

component_descriptor_file = pathlib.Path(
  util.check_env('COMPONENT_DESCRIPTOR_DIR'),
  'component_descriptor'
)

component_descriptor = product.model.Product.from_dict(
  raw_dict=util.parse_yaml_file(component_descriptor_file)
)

processing_mode = ProcessingMode('${image_scan_trait.processing_mode()}')

image_filter = image_reference_filter(
  include_regexes=${filter_cfg.include_image_references()},
  exclude_regexes=${filter_cfg.exclude_image_references()},
)

image_references = [
  ci.image_reference()
  for _, ci
  in product.util._enumerate_images(
    component_descriptor=component_descriptor,
    image_reference_filter=image_filter,
  )
]

relevant_results, license_report = protecode.util.upload_images(
  protecode_cfg=protecode_cfg,
  product_descriptor=component_descriptor,
  processing_mode=processing_mode,
  protecode_group_id=protecode_group_id,
  parallel_jobs=${image_scan_trait.parallel_jobs()},
  cve_threshold=${image_scan_trait.cve_threshold()},
  image_reference_filter=image_filter,
% if upload_registry_prefix:
  upload_registry_prefix='${upload_registry_prefix}',
% endif
  reference_group_ids=${image_scan_trait.reference_protecode_group_ids()},
)

def create_license_report(license_report):
  def to_table_row(upload_result, licenses):
    component_name = upload_result.result.display_name()
    license_names = {license.name() for license in licenses}
    license_names_str = ', '.join(license_names)
    yield (component_name, license_names_str)

  license_lines = [
    to_table_row(upload_result, licenses)
    for upload_result, licenses in license_report
  ]

  print(tabulate.tabulate(
    license_lines,
    headers=('Component Name', 'Licenses'),
    )
  )

  return license_lines

util.info('running virus scan for all container images')
images_with_potential_virusses = tuple(virus_scan_images(image_references))
if images_with_potential_virusses:
  util.warning('Potential virusses found')
else:
  util.info(f'{len(image_references)} image(s) scanned for virus signatures w/o any matches')

# XXX also include in email
report_lines = create_license_report(license_report=license_report)

if not relevant_results and not images_with_potential_virusses:
  sys.exit(0)
email_recipients = ${image_scan_trait.email_recipients()}
if not email_recipients:
  util.warning('Relevant Vulnerabilities were found, but there are no mail recipients configured')
  sys.exit(0)

# notify about critical vulnerabilities

def process_upload_results(upload_result):
  # upload_result tuple of AnalysisResult and CVE Score
  analysis_result = upload_result[0]
  greatest_cve = upload_result[1]

  name = analysis_result.display_name()
  analysis_url = f'{protecode_cfg.api_url()}/products/{analysis_result.product_id()}/#/analysis'
  link_to_analysis_url = f'<a href="{analysis_url}">{name}</a>'

  custom_data = analysis_result.custom_data()
  if custom_data is not None:
    image_reference = custom_data.get('IMAGE_REFERENCE')
  else:
    image_reference = None

  return [link_to_analysis_url, greatest_cve, image_reference]


# component_name identifies the landscape that has been scanned
component_name = "${component_trait.component_name()}"
body = textwrap.dedent(
  f'''
  <p>
  Note: you receive this E-Mail, because you were configured as a mail recipient in repository
  "${component_trait.component_name()}" (see .ci/pipeline_definitions)
  To remove yourself, search for your e-mail address in said file and remove it.
  </p>
  <p>
  The following components in Protecode-group <a href="{protecode_group_url}">{protecode_group_id}</a>
  were found to contain critical vulnerabilities:
  </p>
  '''
)
body += tabulate.tabulate(
  map(process_upload_results, relevant_results),
  headers=('Component Name', 'Greatest CVE', 'Container Image Reference'),
  tablefmt='html',
)

if images_with_potential_virusses:
  body += '<p><div>Virus Scanning results</div>'
  body += tabulate.tabulate(
    images_with_potential_virusses,
    headers=('Image-Reference', 'Scanning Result'),
    tablefmt='html',
  )
else:
  body += f'<p>Scanned {len(image_references)} container image(s) for matching virus signatures '
  body += 'without any matches (id est: all container images seem to be free of known malware)'

mailutil._send_mail(
  email_cfg=cfg_set.email(),
  recipients=email_recipients,
  mail_template=body,
  subject=f'[Action Required] landscape {component_name} has critical Vulnerabilities',
  mimetype='html',
)
util.info('sent notification emails to: ' + ','.join(email_recipients))
</%def>

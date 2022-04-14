<%def
  name="os_id_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
import concourse.steps

image_scan_trait = job_variant.trait('image_scan')
os_id = image_scan_trait.os_id()
component_trait = job_variant.trait('component_descriptor')
root_component_name = component_trait.component_name()

delivery_svc_cfg_name = cfg_set.delivery_endpoints().name()
%>

import ccc.delivery
import ccc.oci
import cnudie.retrieve

${concourse.steps.step_lib('os_id')}
${concourse.steps.step_lib('component_descriptor_util')}

component_descriptor = parse_component_descriptor()
delivery_db_client = ccc.delivery.default_client_if_available()
oci_client = ccc.oci.oci_client()

if not '${delivery_svc_cfg_name}':
  logger.error('no deliverydb-client available - exiting now')
  exit(1)

info_count = 0

for component, resource, os_info in determine_os_ids(
  component_descriptor=component_descriptor,
  oci_client=oci_client,
):
  logger.info(f'uploading os-info for {component.name} {resource.name}')
  upload_to_delivery_db(
    db_client=delivery_db_client,
        resource=resource,
        component=component,
        os_info=os_info,
  )
  info_count += 1

logger.info(f'uploaded {info_count=} os-infos')
</%def>

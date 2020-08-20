import logging
import urllib.parse

try:
    from grafeas.grafeas_v1.gapic.transports.grafeas_grpc_transport import GrafeasGrpcTransport
    from grafeas.grafeas_v1.gapic.grafeas_client import GrafeasClient
    from google.cloud.devtools.containeranalysis_v1.gapic.container_analysis_client import (
        ContainerAnalysisClient,
    )
    from grafeas.grafeas_v1.gapic.enums import (
        DiscoveryOccurrence,
        Severity,
    )
except ModuleNotFoundError:
    from google.cloud.devtools.containeranalysis_v1 import ContainerAnalysisClient
    from grafeas.grafeas_v1.services.grafeas.transports import GrafeasGrpcTransport
    from grafeas.grafeas_v1.services.grafeas.client import GrafeasClient
    from grafeas.grafeas_v1 import (
        DiscoveryOccurrence,
        Severity,
    )

import ci.util
import container.registry
import model.container_registry


def logger():
    return logging.getLogger(__name__)


class VulnerabilitiesRetrievalFailed(RuntimeError):
    pass


def grafeas_client(container_registry_cfg: model.container_registry.ContainerRegistryConfig):
    credentials = container_registry_cfg.credentials()

    default_oauth_scope = (
        'https://www.googleapis.com/auth/cloud-platform',
    )

    try:
        service_address = ContainerAnalysisClient.SERVICE_ADDRESS
        transport = GrafeasGrpcTransport(
            address=service_address,
            scopes=default_oauth_scope, # XXX hard-code for now
            credentials=credentials.service_account_credentials(),
        )

    except AttributeError:
        service_address = ContainerAnalysisClient.DEFAULT_ENDPOINT
        service_port = 443

        transport = GrafeasGrpcTransport(
            host=f'{service_address}:{service_port}',
            scopes=default_oauth_scope, # XXX hard-code for now
            credentials=credentials.service_account_credentials(),
        )

    return GrafeasClient(transport=transport)


def grafeas_client_for_image(image_reference: str):
    image_reference = container.registry.normalise_image_reference(image_reference)

    registry_cfg = model.container_registry.find_config(image_reference=image_reference)
    if not registry_cfg:
        raise VulnerabilitiesRetrievalFailed(f'no registry-cfg found for: {image_reference}')
    if not registry_cfg.has_service_account_credentials():
        raise VulnerabilitiesRetrievalFailed(f'no gcr-cfg ({registry_cfg.name()} {image_reference}')

    logger().info(f'using {registry_cfg.name()}')

    client = grafeas_client(container_registry_cfg=registry_cfg)

    return client


def scan_available(
    image_reference: str,
):
    image_reference = container.registry.normalise_image_reference(image_reference)
    try:
        client = grafeas_client_for_image(image_reference=image_reference)
    except VulnerabilitiesRetrievalFailed as vrf:
        ci.util.warning(f'no gcr-cfg for: {image_reference}: {vrf}')
        # ignore
        return False

    # XXX / HACK: assuming we always handle GCRs (we should rather check!), the first URL path
    # element is the GCR project name
    project_name = urllib.parse.urlparse(image_reference).path.split('/')[1]
    try:
        hash_reference = container.registry.to_hash_reference(image_reference)
    except Exception as e:
        ci.util.warning(f'failed to determine hash for for {image_reference}: {e}')
        return False

    # shorten enum name
    AnalysisStatus = DiscoveryOccurrence.AnalysisStatus
    ContinuousAnalysis = DiscoveryOccurrence.ContinuousAnalysis

    filter_str = f'resourceUrl = "https://{hash_reference}" AND kind="DISCOVERY"'
    try:
        results = list(client.list_occurrences(f'projects/{project_name}', filter=filter_str))
        if (r_count := len(results)) == 0:
            ci.util.warning(f'found no discovery-info for {image_reference}')
            return False
        elif r_count > 1:
            # use latest
            ts_seconds = -1
            candidate = None
            for r in results:
                ts_seconds = max(ts_seconds, r.update_time.seconds)
                if ts_seconds == r.update_time.seconds:
                    candidate = r
            discovery = candidate.discovery
        else:
            discovery = results[0].discovery

        discovery_status = AnalysisStatus(discovery.analysis_status)
        continuous_analysis = ContinuousAnalysis(discovery.continuous_analysis)

        # XXX hard-code we require continuous scanning to be enabled
        if not continuous_analysis is ContinuousAnalysis.ACTIVE:
            ci.util.warning(f'{continuous_analysis=} for {image_reference}')
            return False

        if not discovery_status is AnalysisStatus.FINISHED_SUCCESS:
            ci.util.warning(f'{discovery_status=} for {image_reference}')
            return False

        return True # finally
    except Exception as e:
        ci.util.warning(
            f'error whilst trying to determine discovery-status for {image_reference}: {e}'
        )
        return False


def retrieve_vulnerabilities(
    image_reference: str,
):
    image_reference = container.registry.normalise_image_reference(image_reference)
    client = grafeas_client_for_image(image_reference=image_reference)

    # XXX / HACK: assuming we always handle GCRs (we should rather check!), the first URL path
    # element is the GCR project name
    project_name = urllib.parse.urlparse(image_reference).path.split('/')[1]
    try:
        hash_reference = container.registry.to_hash_reference(image_reference)
    except Exception as e:
        raise VulnerabilitiesRetrievalFailed(e)

    logger().info(f'retrieving vulnerabilites for {project_name} / {hash_reference}')

    filter_str = f'resourceUrl = "https://{hash_reference}" AND kind="VULNERABILITY"'

    try:
        for r in client.list_occurrences(f'projects/{project_name}', filter=filter_str):
            yield r
    except Exception as e:
        raise VulnerabilitiesRetrievalFailed(e)


# shorten default value
SEVERITY_UNSPECIFIED = Severity.SEVERITY_UNSPECIFIED


def filter_vulnerabilities(
    image_reference: str,
    cvss_threshold: int=7.0,
    effective_severity_threshold: Severity=SEVERITY_UNSPECIFIED
):
    for r in retrieve_vulnerabilities(image_reference=image_reference):
        # r has type grafeas.grafeas_v1.types.Occurrence
        vuln = r.vulnerability # grafeas.grafeas_v1.types.VulnerabilityOccurrence
        if not hasattr(vuln, 'cvss_score'):
            continue
        if vuln.cvss_score < cvss_threshold:
            continue
        if hasattr(vuln, 'effective_severity'):
            eff_sev = Severity(vuln.effective_severity)
            if not eff_sev is Severity.SEVERITY_UNSPECIFIED and \
              eff_sev < effective_severity_threshold:
                continue
            else:
                pass # either not specified, or too severe - do not filter out

        # return everything that was not filtered out
        yield r

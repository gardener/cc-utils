import functools
import logging
import urllib

import google.oauth2.service_account as service_account
import googleapiclient.discovery
import google.cloud.storage

import container.registry
import model.container_registry

import ci.util
from .grafeas_model import (
    AnalysisStatus,
    ContinuousAnalysis,
    ListOccurrencesResponse,
    Severity,
)


def logger():
    return logging.getLogger(__name__)


logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)


def _to_gcp_cfg(gcp_cfg: str):
    if isinstance(gcp_cfg, str):
        cfg_factory = ci.util.ctx().cfg_factory()
        gcp_cfg = cfg_factory.gcp(gcp_cfg)
    return gcp_cfg


def credentials(gcp_cfg: str):
    gcp_cfg = _to_gcp_cfg(gcp_cfg=gcp_cfg)

    credentials = service_account.Credentials.from_service_account_info(
        gcp_cfg.service_account_key(),
    )

    return credentials


def authenticated_build_func(gcp_cfg: str):
    creds = credentials(gcp_cfg=gcp_cfg)

    return functools.partial(googleapiclient.discovery.build, credentials=creds)


def cloud_storage_client(gcp_cfg: str, *args, **kwargs):
    gcp_cfg = _to_gcp_cfg(gcp_cfg=gcp_cfg)
    creds = credentials(gcp_cfg=gcp_cfg)

    return google.cloud.storage.Client(
        project=gcp_cfg.project(),
        credentials=creds,
        *args,
        **kwargs,
    )


CONTAINERANALYSIS_DEFAULT_AUTH_SCOPES = ('https://www.googleapis.com/auth/cloud-platform',)


class VulnerabilitiesRetrievalFailed(RuntimeError):
    pass


class GrafeasClient:
    def __init__(
        self,
        container_registry_config,
        scopes=CONTAINERANALYSIS_DEFAULT_AUTH_SCOPES,
    ):
        credentials = container_registry_config.credentials().service_account_credentials()
        scoped_credentials = credentials.with_scopes(scopes)
        self._api_client =  googleapiclient.discovery.build(
            'containeranalysis',
            'v1',
            credentials=scoped_credentials,
        )

    @staticmethod
    def for_image(
        image_reference: str,
        scopes=CONTAINERANALYSIS_DEFAULT_AUTH_SCOPES,
    ):
        '''Convenience function for client creation

        NOTE: Will determine credentials to use from image reference. The created client's methods
        will *not* work for other images to which the determined credentials have no access to.
        '''
        image_reference = container.registry.normalise_image_reference(image_reference)
        registry_config = model.container_registry.find_config(image_reference=image_reference)

        if not registry_config:
            raise VulnerabilitiesRetrievalFailed(f'no registry-cfg found for: {image_reference}')
        if not registry_config.has_service_account_credentials():
            raise VulnerabilitiesRetrievalFailed(
                f'no gcr-cfg {registry_config.name()} {image_reference}'
            )

        return GrafeasClient(
            container_registry_config=registry_config,
            scopes=scopes,
        )

    def _list_occurrences(self, project, filter_expression):
        projects = self._api_client.projects()
        occurrences = projects.occurrences()

        request = occurrences.list(
            parent=project,
            filter=filter_expression,
        )
        while request is not None:
            response = request.execute()
            parsed_response = ListOccurrencesResponse.parse(response)
            yield from parsed_response.occurrences

            request = occurrences.list_next(request, response)

    def _parse_gcr_parameters(self, image_reference):
        project_name = urllib.parse.urlparse(image_reference).path.split('/')[1]
        try:
            hash_reference = container.registry.to_hash_reference(image_reference)
        except Exception as e:
            raise VulnerabilitiesRetrievalFailed(e)

        return project_name, hash_reference

    def retrieve_vulnerabilities(
        self,
        image_reference: str,
    ):
        # XXX / HACK: assuming we always handle GCRs (we should rather check!), the first URL path
        # element is the GCR project name
        project_name, hash_reference = self._parse_gcr_parameters(image_reference)

        logger().info(f'retrieving vulnerabilites for {project_name=} / {hash_reference=}')

        filter_str = f'resourceUrl = "https://{hash_reference}" AND kind="VULNERABILITY"'

        occurrences = self._list_occurrences(
            project=f'projects/{project_name}',
            filter_expression=filter_str,
        )

        yield from occurrences

    def filter_vulnerabilities(
        self,
        image_reference: str,
        cvss_threshold: float=7.0,
        effective_severity_threshold: Severity=Severity.SEVERITY_UNSPECIFIED
    ):
        try:
            vulnerabilityOccurrences = self.retrieve_vulnerabilities(image_reference=image_reference)
        except Exception as e:
            raise VulnerabilitiesRetrievalFailed(e)

        for occurrence in vulnerabilityOccurrences:
            vulnerability = occurrence.vulnerability

            if vulnerability.cvssScore < cvss_threshold:
                continue

            if (
                vulnerability.effectiveSeverity is not Severity.SEVERITY_UNSPECIFIED
                and vulnerability.effectiveSeverity < effective_severity_threshold
            ):
                continue

            # return everything that was not filtered out
            yield occurrence

    def scan_available(
        self,
        image_reference: str,
    ):
        image_reference = container.registry.normalise_image_reference(image_reference)

        # XXX / HACK: assuming we always handle GCRs (we should rather check!), the first URL path
        # element is the GCR project name
        project_name, hash_reference = self._parse_gcr_parameters(image_reference)

        filter_str = f'resourceUrl = "https://{hash_reference}" AND kind="DISCOVERY"'

        discoveries = list(self._list_occurrences(
            project=f'projects/{project_name}',
            filter_expression=filter_str,
        ))

        if (discovery_count := len(discoveries)) == 0:
            logger().warning(f'found no discovery-info for {image_reference=}')
            return False
        elif discovery_count > 1:
            # use latest
            timestamp = -1
            candidate = None
            for d in discoveries:
                timestamp = max(timestamp, d.updateTime.timestamp())
                if timestamp == d.updateTime().timestamp():
                    candidate = d
        else:
            candidate = discoveries[0]

        discovery = candidate.discovery

        discovery_status = discovery.analysisStatus
        continuous_analysis = discovery.continuousAnalysis

        # XXX hard-code we require continuous scanning to be enabled
        if continuous_analysis is not ContinuousAnalysis.ACTIVE:
            logger().warning(f'{continuous_analysis=} for {image_reference=}')
            return False

        if discovery_status is not AnalysisStatus.FINISHED_SUCCESS:
            logger().warning(f'{discovery_status=} for {image_reference=}')
            return False

        return True # finally

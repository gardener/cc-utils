import google.cloud.devtools.containeranalysis_v1
import grafeas.grafeas_v1
import grafeas.grafeas_v1.gapic.transports.grafeas_grpc_transport

import model.container_registry

gcrp_transport = grafeas.grafeas_v1.gapic.transports.grafeas_grpc_transport
container_analysis_v1 = google.cloud.devtools.containeranalysis_v1


def grafeas_client(credentials: model.container_registry.GcrCredentials):
    service_address = container_analysis_v1.ContainerAnalysisClient.SERVICE_ADDRESS
    default_oauth_scope = (
        'https://www.googleapis.com/auth/cloud-platform',
    )
    transport = gcrp_transport.GrafeasGrpcTransport(
        address=service_address,
        scopes=default_oauth_scope, # XXX hard-code for now
        credentials=credentials.service_account_credentials(),
    )

    return grafeas.grafeas_v1.GrafeasClient(transport)

import jwt as jwt_mod # avoid overwriting delivery.jwt
import time

import delivery.client


def _create_github_jwt(
    github_app_id: int,
    github_app_private_key: str | bytes,
    ttl_seconds: int=600, # 10m
    algorithm: str='RS256',
) -> str:
    if isinstance(github_app_private_key, str):
        github_app_private_key = github_app_private_key.encode('utf-8')

    github_app_id = int(github_app_id) # validate input
    now = int(time.time())

    payload = {
        'iat': now,
        'exp': now + ttl_seconds,
        'iss': github_app_id,
    }

    return jwt_mod.encode(
        payload=payload,
        key=github_app_private_key,
        algorithm=algorithm,
    )


def client_from_github_app_secret(
    github_app_id: int,
    github_app_private_key: str | bytes,
    github_api_url: str,
    delivery_service_base_url: str,
) -> delivery.client.DeliveryServiceClient:
    '''
    an opinionated factory-function creating DeliveryService-Client-instances using a
    GitHub-App for authentication. This is deemed especially useful for usage in GitHub-Actions
    pipelines, where usage of Service-Accounts is not desirable.

    github_api_url must match the GH(E)-Instance the used GitHub-App is installed on. The targettted
    Delivery-Service must offer a configuration for this GH(E)-Instance.
    '''
    def token_lookup(api_url: str, /):
        if api_url != github_api_url:
            return None

        return _create_github_jwt(
            github_app_id=github_app_id,
            github_app_private_key=github_app_private_key,
        )

    return delivery.client.DeliveryServiceClient(
        routes=delivery.client.DeliveryServiceRoutes(
            base_url=delivery_service_base_url,
        ),
        auth_token_lookup=token_lookup,
    )

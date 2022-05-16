import copy
import dataclasses
import dateutil.parser
import typing
import logging

import msal
import dacite
import requests

import cfg_mgmt
import ci.log
import model
import model.azure

from cfg_mgmt.model import CfgQueueEntry


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


@dataclasses.dataclass
class PasswordCredential:
    displayName: str
    keyId: str
    endDateTime: str
    startDateTime: typing.Optional[str]
    customKeyIdentifier: typing.Optional[str]
    hint: typing.Optional[str]
    secretText: typing.Optional[str]  # the actual password, if present

    def end_DateTime(self):
        return dateutil.parser.isoparse(self.endDateTime)

    def start_DateTime(self):
        return dateutil.parser.isoparse(self.startDateTime)


def _get_access_token_for_principal(
    service_principal: model.azure.AzureServicePrincipal,
):

    app = msal.ConfidentialClientApplication(
        client_id=service_principal.client_id(),
        authority=f'https://login.microsoftonline.com/{service_principal.tenant_id()}',
        client_credential=service_principal.client_secret(),
    )
    scopes = ['https://graph.microsoft.com/.default',]
    response = app.acquire_token_for_client(scopes=scopes)

    if 'access_token' in response:
        access_token = response['access_token']
    elif 'error' in response:
        raise RuntimeError(
            f'Error authenticating with Microsoft Graph: {response.get("error_description")}'
        )
    else:
        raise RuntimeError(f'Unexpected response: {response}')

    return access_token


def _retrieve_password_credentials(
    service_principal: model.azure.AzureServicePrincipal,
    access_token: str,
    azure_app_root_url: str = 'https://graph.microsoft.com/v1.0/applications'
) -> typing.Iterable[PasswordCredential]:
    response = requests.get(
        url=f'{azure_app_root_url}/{service_principal.object_id()}',
        headers={'Authorization': f'Bearer {access_token}'},
    )
    response_dict = response.json()
    if not (password_credentials := response_dict.get('passwordCredentials')):
        # no keys
        logger.warning(
            f"No password-credentials found for service principal '{service_principal.name()}'"
        )
        return []

    return [PasswordCredential(**e) for e in password_credentials]


def _add_password_credential(
    service_principal: model.azure.AzureServicePrincipal,
    display_name: str,
    access_token: str,
    azure_app_root_url: str = 'https://graph.microsoft.com/v1.0/applications'
) -> PasswordCredential:
    '''Add a new password credential to the given service principal and return the corresponding
    PasswordCredential object

    Note: This will be the only time that the generated `secretText` (i.e. the actual password) is
    available - no other calls to the API populate this field.
    '''
    body = {"passwordCredential": {"displayName": display_name}}

    response = requests.post(
        f'{azure_app_root_url}/{service_principal.object_id()}/addPassword',
        json=body,
        headers={'Authorization': f'Bearer {access_token}'},
    )
    response.raise_for_status()
    response_dict = response.json()

    return dacite.from_dict(
        data_class=PasswordCredential,
        data=response_dict,
    )


def _remove_password_credential(
    service_principal: model.azure.AzureServicePrincipal,
    key_id: str,
    access_token: str,
    azure_app_root_url: str = 'https://graph.microsoft.com/v1.0/applications'
):
    response = requests.post(
        f'{azure_app_root_url}/{service_principal.object_id()}/removePassword',
        json={"keyId": key_id},
        headers={'Authorization': f'Bearer {access_token}'},
    )
    # successful request returns "204 No content"
    response.raise_for_status()


def rotate_cfg_element(
    cfg_element: model.azure.AzureServicePrincipal,
    cfg_factory: model.ConfigFactory,
) ->  typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:

    access_token = _get_access_token_for_principal(
        service_principal=cfg_element,
    )

    known_password_credentials = _retrieve_password_credentials(
        service_principal=cfg_element,
        access_token=access_token,
    )

    matching_password_credentials = [
        p for p in known_password_credentials if cfg_element.client_secret().startswith(p.hint)
    ]

    # since we used the client secret to authenticate against the API we can be certain that
    # at least one password matches
    if len(matching_password_credentials) > 1:
        raise RuntimeError('Unable to unambiguously determine old password credential, aborting.')

    current_password_credential = matching_password_credentials[0]
    new_password_credential = _add_password_credential(
        service_principal=cfg_element,
        display_name='CI Access',
        access_token=access_token,
    )

    raw_cfg = copy.deepcopy(cfg_element.raw)
    new_element = model.azure.AzureServicePrincipal(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )
    new_element.raw['client_secret'] = new_password_credential.secretText

    secret_id = {'keyId': current_password_credential.keyId}

    def revert_function():
        _remove_password_credential(
            service_principal=cfg_element,
            key_id=new_password_credential.keyId,
            access_token=access_token,  # token is good for 20 minutes by default,
                                        # so it's safe to reuse here
        )

    return revert_function, secret_id, new_element


def delete_config_secret(
    cfg_element: model.azure.AzureServicePrincipal,
    cfg_factory: model.ConfigFactory,
    cfg_queue_entry: CfgQueueEntry,
):
    key_id = cfg_queue_entry.secretId['keyId']
    access_token = _get_access_token_for_principal(service_principal=cfg_element)
    _remove_password_credential(
        service_principal=cfg_element,
        key_id=key_id,
        access_token=access_token,
    )

import copy
import logging
import typing
import requests
from dataclasses import dataclass

import cfg_mgmt
import cfg_mgmt.model as cmm
import ci.log
import ci.util
import model
import model.btp_service_binding
import model.container_registry


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


@dataclass
class ServiceBindingInfo:
    id: str
    name: str
    service_instance_id: str


def _find_next_service_binding_serial_number(
    bindings: list[ServiceBindingInfo],
    binding_name_prefix: str,
    instance_id: str,
) -> int:
    next_sn = 1
    for item in bindings:
        if item.name.startswith(binding_name_prefix) and item.service_instance_id == instance_id:
            try:
                n = int(item.name[len(binding_name_prefix):])
                if n >= next_sn:
                    next_sn = n + 1
            except ValueError:
                logger.warn(f'ignored {item.name}')
    return next_sn


class SBClient:
    def __init__(
        self,
        sm_url: str,
        access_token,
    ):
        self.sm_url = sm_url
        self.access_token = access_token

    def delete_service_binding(self, name: str, id: str):
        headers = {
            'Authorization': f'Bearer {self.access_token}',
        }
        url = f'{self.sm_url}/v1/service_bindings/{id}'
        resp = requests.delete(url, headers=headers)
        if not resp.ok:
            msg = f'delete_service_binding failed: {resp.status_code} {resp.text}'
            logger.error(msg)
            raise requests.HTTPError(msg)
        logger.info(f'Deleted service binding {name} ({id})')

    def create_service_binding(self, instance_id: str, binding_name: str) -> tuple[str, dict]:
        url = f'{self.sm_url}/v1/service_bindings'
        data = {
            'name': binding_name,
            'service_instance_id': instance_id,
        }
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.access_token}',
        }
        resp = requests.post(url, json=data, headers=headers)
        if not resp.ok:
            msg = f'create_service_binding failed: {resp.status_code} {resp.text}'
            logger.error(msg)
            raise requests.HTTPError(msg)
        result = resp.json()
        id = result['id']
        logger.info(f'Creating service binding {binding_name} ({id})')
        return id, result['credentials']

    def get_service_bindings(self) -> list[ServiceBindingInfo]:
        url = f'{self.sm_url}/v1/service_bindings'
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.access_token}',
        }
        resp = requests.get(url, headers=headers)
        if not resp.ok:
            msg = f'get_service_bindings failed: {resp.status_code} {resp.text}'
            logger.error(msg)
            raise requests.HTTPError(msg)
        bindings = []
        for item in resp.json()['items']:
            id = item['id']
            name = item['name']
            instance_id = item['service_instance_id']
            bindings.append(ServiceBindingInfo(id=id, name=name, service_instance_id=instance_id))
        return bindings


def _get_oauth_token(credentials: dict) -> str:
    url = f'{credentials["url"]}/oauth/token'
    data = {
        'grant_type': 'client_credentials',
        'token_format': 'bearer',
        'client_id': credentials['clientid'],
        'client_secret': credentials['clientsecret'],
    }
    headers = {
        'Accept': 'application/json',
    }
    resp = requests.post(url, data=data, headers=headers)
    if not resp.ok:
        msg = f'_get_oauth_token failed: {resp.status_code} {resp.reason}'
        logger.error(msg)
        raise requests.HTTPError(msg)
    result = resp.json()
    return result['access_token']


def _authenticate(
    cfg_element: model.btp_service_binding.BtpServiceBinding,
    cfg_factory: model.ConfigFactory,
) -> SBClient:
    auth = cfg_element.auth_service_binding()
    credentials = cfg_factory.btp_service_binding(auth).credentials()
    sm_url = credentials['sm_url']
    access_token = _get_oauth_token(credentials)
    return SBClient(sm_url, access_token)


def rotate_cfg_element(
    cfg_element: model.btp_service_binding.BtpServiceBinding,
    cfg_factory: model.ConfigFactory,
) -> typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:
    old_binding_id = cfg_element.binding_id()
    old_binding_name = cfg_element.binding_name()
    client = _authenticate(cfg_element, cfg_factory)
    bindings = client.get_service_bindings()
    prefix = cfg_element.prefix()
    instance_id = cfg_element.instance_id()
    next_sn = _find_next_service_binding_serial_number(bindings, prefix, instance_id)
    binding_name = f'{prefix}{next_sn}'
    id, newcreds = client.create_service_binding(instance_id, binding_name)

    secret_id = {'binding_id': old_binding_id, 'binding_name': old_binding_name}
    raw_cfg = copy.deepcopy(cfg_element.raw)
    raw_cfg['credentials'] = newcreds
    raw_cfg['binding_id'] = id
    raw_cfg['binding_name'] = binding_name
    updated_elem = model.btp_service_binding.BtpServiceBinding(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )

    def revert():
        client.delete_service_binding(binding_name, id)

    return revert, secret_id, updated_elem


def delete_config_secret(
    cfg_element: model.btp_service_binding.BtpServiceBinding,
    cfg_queue_entry: cmm.CfgQueueEntry,
    cfg_factory: model.ConfigFactory,
):
    logger.info('deleting old service binding')
    id = cfg_queue_entry.secretId.get('binding_id')
    if id:
        name = cfg_queue_entry.secretId['binding_name']
        client = _authenticate(cfg_element, cfg_factory)
        client.delete_service_binding(name, id)

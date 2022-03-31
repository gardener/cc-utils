import copy
import json
import logging
import typing
import requests

import cfg_mgmt
import cfg_mgmt.model as cmm
import ci.log
import ci.util
import model
import model.container_registry


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def _filter_service_bindings(
    result: dict,
    binding_name_prefix: str,
    instance_id: str,
) -> tuple[list[str], int]:
    outdated_ids = []
    next = 1
    for item in result["items"]:
        name = item["name"]
        if name.startswith(binding_name_prefix) and item["service_instance_id"] == instance_id:
            try:
                n = int(name[len(binding_name_prefix):])
                outdated_ids.append(item["id"])
                if n >= next:
                    next = n + 1
            except ValueError:
                logger.warn("ignored {}".format(name))
    return outdated_ids, next


class sbClient:
    def __init__(
        self,
        sm_url: str,
        access_token,
    ):
        self.sm_url = sm_url
        self.access_token = access_token

    def delete_service_binding(self, name: str, id: str):
        headers = {
            'Authorization': 'Bearer {}'.format(self.access_token),
        }
        url = "{}/v1/service_bindings/{}".format(self.sm_url, id)
        resp = requests.delete(url, headers=headers)
        if resp.status_code != 200:
            msg = 'delete_service_binding failed: {} {}'.format(resp.status_code, resp.text)
            logger.error(msg)
            raise requests.HTTPError(msg)
        logger.info("Deleted service binding {} ({})".format(name, id))

    def create_service_binding(self, instance_id: str, binding_name: str) -> tuple[str, dict]:
        url = "{}/v1/service_bindings".format(self.sm_url)
        data = {
            "name": binding_name,
            "service_instance_id": instance_id,
        }
        headers = {
            'Accept': 'application/json',
            'Authorization': 'Bearer {}'.format(self.access_token),
            'Content-Type': 'application/json',
        }
        resp = requests.post(url, data=json.dumps(data), headers=headers)
        if resp.status_code != 201:
            msg = 'create_service_binding failed: {} {}'.format(resp.status_code, resp.text)
            logger.error(msg)
            raise requests.HTTPError(msg)
        result = resp.json()
        id = result["id"]
        logger.info('Creating service binding {} ({})'.format(binding_name, id))
        return id, result["credentials"]

    def get_service_bindings(self) -> dict:
        url = "{}/v1/service_bindings".format(self.sm_url)
        headers = {
            'Accept': 'application/json',
            'Authorization': 'Bearer {}'.format(self.access_token),
        }
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            msg = 'get_service_bindings failed: {} {}'.format(resp.status_code, resp.text)
            logger.error(msg)
            raise requests.HTTPError(msg)
        list = resp.json()
        return list


def _get_oauth_token(credentials: dict) -> str:
    url = '{}/oauth/token'.format(credentials['url'])
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
    if resp.status_code != 200:
        msg = '_get_oauth_token failed: {} {}'.format(resp.status_code, resp.reason)
        logger.error(msg)
        raise requests.HTTPError(msg)
    result = resp.json()
    return result['access_token']


def _authenticate(
    cfg_element: model.btp_service_binding.BtpServiceBinding,
    cfg_factory: model.ConfigFactory,
) -> sbClient:
    auth = cfg_element.auth_service_binding()
    credentials = cfg_factory.btp_service_binding(auth).credentials()
    sm_url = credentials['sm_url']
    access_token = _get_oauth_token(credentials)
    return sbClient(sm_url, access_token)


def rotate_cfg_element(
    cfg_element: model.btp_service_binding.BtpServiceBinding,
    cfg_factory: model.ConfigFactory,
) -> typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:
    client = _authenticate(cfg_element, cfg_factory)
    result = client.get_service_bindings()
    prefix = cfg_element.prefix()
    instance_id = cfg_element.instance_id()
    _, next = _filter_service_bindings(result, prefix, instance_id)
    binding_name = "{}{}".format(prefix, next)
    id, newcreds = client.create_service_binding(instance_id, binding_name)

    secret_id = {'binding_id': id, 'binding_name': binding_name}
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
    client = _authenticate(cfg_element, cfg_factory)
    name = cfg_queue_entry.secretId['binding_name']
    id = cfg_queue_entry.secretId['id']
    client.delete_service_binding(name, id)

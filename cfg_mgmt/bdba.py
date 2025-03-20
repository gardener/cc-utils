import datetime
import logging

import requests

import cfg_mgmt
import model.bdba

logger = logging.getLogger(__name__)


def get_api_key_expiry(
    cfg_element: model.bdba.BDBAConfig
) -> datetime.datetime:
    credentials = cfg_element.credentials()
    headers = {'Authorization': f'Bearer {credentials.token()}'}

    response = requests.get(
        f'{cfg_element.api_url()}/api/key/',
        headers=headers,
        verify=cfg_element.tls_verify(),
        timeout=30,
    )

    response.raise_for_status()

    key_info = response.json()
    expiry_timestamp = key_info['key'].get('expires')

    if not expiry_timestamp:
        raise ValueError('No expiration timestamp found for the API key.')

    return datetime.datetime.fromisoformat(expiry_timestamp).replace(tzinfo=datetime.timezone.utc)


def rotate_cfg_element(
    cfg_element: model.bdba.BDBAConfig,
    cfg_factory: model.ConfigFactory,
) -> tuple[cfg_mgmt.revert_function, dict, model.bdba.BDBAConfig]:
    logger.info(f'Rotating API key for {cfg_element.name()}')

    credentials = cfg_element.credentials()
    headers = {'Authorization': f'Bearer {credentials.token()}'}

    # Request a new API key
    response = requests.post(
        f'{cfg_element.api_url()}/api/key/',
        json={'validity': 15379200},  # 178 days
        headers=headers,
        verify=cfg_element.tls_verify(),
        timeout=30,
    )
    response.raise_for_status()

    new_key_info = response.json()
    logger.info(f'New API key response: {new_key_info}')

    new_key = new_key_info['key']['value']

    raw_cfg = cfg_element.raw.copy()
    raw_cfg['credentials']['token'] = new_key

    updated_cfg_element = model.bdba.BDBAConfig(
        name=cfg_element.name(),
        raw_dict=raw_cfg,
        type_name=cfg_element._type_name
    )

    secret_id = {'api_key': new_key}

    def no_op():
        logger.warning('No rollback possible for BDBA key rotation.')

    return no_op, secret_id, updated_cfg_element


def delete_config_secret(
    cfg_element: model.bdba.BDBAConfig,
) -> model.bdba.BDBAConfig | None:
    logger.info(f'Deleting API key for {cfg_element.name()}')

    credentials = cfg_element.credentials()
    headers = {'Authorization': f'Bearer {credentials.token}'}

    response = requests.delete(
        f'{cfg_element.api_url()}/api/key/',
        headers=headers,
        verify=cfg_element.tls_verify(),
        timeout=30,
    )

    if response.status_code == 400:
        logger.warning(f'API key for {cfg_element.name()} was already deleted.')
        return None

    response.raise_for_status()

    return None


def validate_for_rotation(cfg_element: model.bdba.BDBAConfig) -> None:
    expiry_date = get_api_key_expiry(cfg_element)
    remaining_days = (expiry_date - datetime.datetime.now(datetime.timezone.utc)).days

    if remaining_days >= 10:
        raise ValueError(f'API key for {cfg_element.name()} does not need rotation')

    logger.warning((f'API key for {cfg_element.name()} expires in {remaining_days} days. '
                    'Proceeding with rotation.'))

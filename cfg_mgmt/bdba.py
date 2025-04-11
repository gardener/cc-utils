import datetime
import logging

import bdba.client
import cfg_mgmt
import model.bdba


logger = logging.getLogger(__name__)


def client(
    cfg: model.bdba.BDBAConfig
) -> bdba.client.BDBAApi:
    return bdba.client.BDBAApi(
        api_routes=bdba.client.BDBAApiRoutes(base_url=cfg.api_url()),
        token=cfg.credentials().token(),
        tls_verify=cfg.tls_verify(),
    )


def expiration_date(
    cfg_element: model.bdba.BDBAConfig
) -> datetime.datetime:
    key_info = client(cfg_element).api_key().json()

    if not (expiry_timestamp := key_info['key'].get('expires')):
        raise ValueError('No expiration timestamp found for the API key.')

    return datetime.datetime.fromisoformat(expiry_timestamp)


def rotate_cfg_element(
    cfg_element: model.bdba.BDBAConfig,
    cfg_factory: model.ConfigFactory,
) -> tuple[cfg_mgmt.revert_function, dict, model.bdba.BDBAConfig]:
    logger.info(f'Rotating API key for {cfg_element.name()}')
    response = client(cfg_element).create_key(validaty_seconds=60 * 60 * 24 * 178)  # 178 days

    new_key_info = response.json()

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
        logger.critical(
            'No rollback possible for BDBA key rotation!\n'
            'Manuel intervention required:\n'
            '1. A new API Key was generated but may not be saved in the config\n'
            '2. The old key is immediatly invalid after rotation\n'
            '3. Check logs for the new key\n'
            '4. Update the config with the new key'
        )

    return no_op, secret_id, updated_cfg_element


def validate_for_rotation(cfg_element: model.bdba.BDBAConfig) -> None:
    remaining_days = (expiration_date(cfg_element) - datetime.datetime.now()).days

    if remaining_days >= 10:
        raise ValueError(f'API key for {cfg_element.name()} does not need rotation')

    logger.warning((f'API key for {cfg_element.name()} expires in {remaining_days} days. '
                    'Proceeding with rotation.'))

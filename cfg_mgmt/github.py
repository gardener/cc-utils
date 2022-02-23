import copy
import logging
import typing

from Crypto.PublicKey import RSA

import ccc.github
import cfg_mgmt
import cfg_mgmt.util
import ci.util
import model
import model.github

from cfg_mgmt.model import CfgQueueEntry
from model.github import (
    GithubConfig,
    GithubCredentials,
)


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def rotate_cfg_element(
    cfg_element: model.container_registry.ContainerRegistryConfig,
    cfg_factory: model.ConfigFactory,
) ->  typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:

    # copy passed cfg_element, since we update in-place.
    raw_cfg = copy.deepcopy(cfg_element.raw)
    cfg_to_rotate = model.github.GithubConfig(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )

    technical_user_credentials = cfg_to_rotate._technical_user_credentials()

    # retrieve current/"old" public keys before updating to be able to store them in deletion
    # queue
    old_public_keys = [
        {
            'name': credential.username(),
            'public_key': _corresponding_public_key(credential.private_key()),
        }
        for credential in technical_user_credentials
    ]

    new_public_keys = {
        credential.username(): update_user(cfg_to_rotate, credential, cfg_factory)
        for credential in technical_user_credentials
    }

    secret_id = {'github_users': old_public_keys}

    def revert():
        logger.warning(
            f"An error occurred during update of github config '{cfg_to_rotate.name()}', "
            'rolling back'
        )
        for credential in technical_user_credentials:
            username = credential.username()
            logger.warning(f"Rolling back changes to user '{username}'")
            new_pub_key = new_public_keys[username]
            gh_api = ccc.github.github_api(
                github_cfg=cfg_to_rotate,
                username=username,
                cfg_factory=cfg_factory,
            )
            for key in gh_api.keys():
                if key.key == new_pub_key:
                    key.delete()
                    logger.info(f"Rollback successful for key '{new_pub_key}'.")
                    break
            else:
                logger.warning(
                    f"New public key for '{username}' not known to github, nothing to revert."
                )

    return revert, secret_id, cfg_to_rotate


def update_user(
    github_config: GithubConfig,
    credential: GithubCredentials,
    cfg_factory: model.ConfigFactory,
) -> str:
    gh_api = ccc.github.github_api(
        github_cfg=github_config,
        username=credential.username(),
        cfg_factory=cfg_factory,
    )
    private_key, public_key = _create_key_pair()
    gh_api.create_key(
        title='Auto-generated key',
        key=public_key,
    )
    credential.raw['privateKey'] = private_key
    return public_key


def delete_config_secret(
    cfg_element: model.github.GithubConfig,
    cfg_factory: model.ConfigFactory,
    cfg_queue_entry: CfgQueueEntry,
):
    for entry in cfg_queue_entry.secretId['github_users']:
        username = entry['name']
        gh_api = ccc.github.github_api(cfg_element, username=username, cfg_factory=cfg_factory)
        for key in gh_api.keys():
            if key.key == entry['public_key']:
                key.delete()
                break
        else:
            logger.warning(
                f'Old public key for {username} not known to github, nothing to delete.'
            )


def _corresponding_public_key(
    private_key: str,
) -> str:
    # current private key uses EdDSA which is not supported by Pycryptodome
    # TODO: Remove after first rotation
    try:
        key = RSA.import_key(private_key)
    except ValueError:
        return ""
    return key.public_key().export_key(format='OpenSSH').decode('utf-8')


def _create_key_pair(
    bits: int = 4096,
) -> typing.Tuple[str, str]:
    '''return a (private-key, public-key)-RSA-keypair as a pair of strings.

    The private-key is returned in PEM-Format whereas the public key is returned as specified by
    OpenSSH (which is the format expected by GitHub)
    '''
    private_key = RSA.generate(bits=bits)
    public_key = private_key.public_key()
    return (
        private_key.export_key().decode('utf-8'),
        public_key.export_key(format='OpenSSH').decode('utf-8')
    )

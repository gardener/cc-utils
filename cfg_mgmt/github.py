import copy
import logging
import typing
import enum

from Crypto.PublicKey import RSA, ECC

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


class KeyAlgorithm(enum.Enum):
    RSA = enum.auto()
    ECC = enum.auto()


def _determine_key_algorithm(key: str) -> KeyAlgorithm:
    try:
        RSA.import_key(key)
        return KeyAlgorithm.RSA
    except ValueError:
        pass

    try:
        ECC.import_key(key)
        return KeyAlgorithm.ECC
    except ValueError:
        pass

    raise ValueError('Unsupported Key format. Currently, only RSA and ECC keys are supported.')


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
    old_public_keys = []
    new_public_keys = dict()
    for credential in technical_user_credentials:

        key_algorithm = _determine_key_algorithm(credential.private_key())

        old_public_keys.append({
            'name': credential.username(),
            'public_key': _corresponding_public_key(
                private_key=credential.private_key(),
                key_algorithm=key_algorithm,
            ),
        })

        new_public_keys.update({
            credential.username(): update_user(
                github_config=cfg_to_rotate,
                credential=credential,
                cfg_factory=cfg_factory,
                key_algorithm=key_algorithm,
            )
        })

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
    key_algorithm: KeyAlgorithm,
) -> str:
    gh_api = ccc.github.github_api(
        github_cfg=github_config,
        username=credential.username(),
        cfg_factory=cfg_factory,
    )
    private_key, public_key = _create_key_pair(key_algorithm=key_algorithm)
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
    key_algorithm: KeyAlgorithm,
) -> str:
    if key_algorithm is KeyAlgorithm.RSA:
        key = RSA.import_key(private_key)
        public_key_str = key.public_key().export_key(format='OpenSSH').decode('utf-8')
    elif key_algorithm is KeyAlgorithm.ECC:
        key = ECC.import_key(private_key)
        # PEM/OpenSSH keys will be returned as string (not necessarily true for other formats)
        public_key_str = key.public_key().export_key(format='OpenSSH')
    else:
        raise NotImplementedError(key_algorithm)

    return public_key_str


def _create_key_pair(
    key_algorithm: KeyAlgorithm,
) -> typing.Tuple[str, str]:
    '''return a (private-key, public-key)-keypair using the given Algorithm as a pair of strings.

    The private-key is returned in PEM-Format whereas the public key is returned as specified by
    OpenSSH (which is the format expected by GitHub).
    '''
    if key_algorithm is KeyAlgorithm.RSA:
        private_key = RSA.generate(bits=4096)
        private_key_str = private_key.export_key(format='PEM').decode("utf-8")
        public_key_str = private_key.public_key().export_key(format='OpenSSH').decode("utf-8")
    elif key_algorithm is KeyAlgorithm.ECC:
        private_key = ECC.generate(curve='ed25519')
        # PEM/OpenSSH keys will be returned as string (not necessarily true for other formats)
        private_key_str = private_key.export_key(format='PEM')
        public_key_str = private_key.public_key().export_key(format='OpenSSH')
    else:
        raise NotImplementedError(key_algorithm)

    return (
        private_key_str,
        public_key_str
    )

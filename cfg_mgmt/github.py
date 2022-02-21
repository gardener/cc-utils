import logging
import os
import typing
import yaml

from Crypto.PublicKey import RSA

import ccc.github
import cfg_mgmt
import cfg_mgmt.util
import ci.util
import model
import model.github

from cfg_mgmt.model import (
    CfgMetadata,
    CfgQueueEntry,
    cfg_status_fname,
)
from model.github import (
    GithubConfig,
    GithubCredentials,
)


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def create_secret_and_persist_in_cfg_repo(
    cfg_dir: str,
    cfg_element: GithubConfig,
    cfg_metadata: CfgMetadata,
) -> cfg_mgmt.revert_function:
    cfg_factory = model.ConfigFactory.from_cfg_dir(cfg_dir, disable_cfg_element_lookup=True)

    local_sources = cfg_mgmt.util.local_cfg_type_sources(
        cfg_element=cfg_element,
        cfg_factory=cfg_factory,
    )

    if len(local_sources) != 1:
        raise NotImplementedError(
            'Do not know how to rotate github configs sourced from more than one file.'
        )

    source_file = next((s for s in local_sources))

    known_github_configs: typing.Iterable[GithubConfig] = [
        c for c in cfg_factory._cfg_elements(
            cfg_type_name=cfg_element._type_name,
        )
    ]

    cfg_name = cfg_element.name()
    if not (cfg_to_rotate := next(
        (c for c in known_github_configs if c.name() == cfg_name),
        None,
    )):
        raise RuntimeError(
            f"Did not find requested config '{cfg_name}' in config dir at '{cfg_dir}'"
        )

    known_github_configs: typing.Iterable[GithubConfig] = [
        c for c in cfg_factory._cfg_elements(
            cfg_type_name=cfg_element._type_name,
        )
    ]

    cfg_name = cfg_element.name()
    if not (cfg_to_rotate := next(
        (c for c in known_github_configs if c.name() == cfg_name),
        None,
    )):
        raise RuntimeError(
            f"Did not find requested config '{cfg_name}' in config dir at '{cfg_dir}'"
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
        credential.username(): update_user(cfg_to_rotate, credential)
        for credential in technical_user_credentials
    }

    _write_github_configs(
        cfg_dir=cfg_dir,
        cfg_file_name=source_file,
        github_configs=known_github_configs,
    )

    cfg_metadata.queue.append(
        cfg_mgmt.util.create_config_queue_entry(
            queue_entry_config_element=cfg_to_rotate,
            queue_entry_data={'github_users': old_public_keys},
        )
    )

    cfg_mgmt.util.write_config_queue(
        cfg_dir=cfg_dir,
        cfg_metadata=cfg_metadata,
    )

    cfg_mgmt.util.update_config_status(
        config_element=cfg_to_rotate,
        config_statuses=cfg_metadata.statuses,
        cfg_status_file_path=os.path.join(
            cfg_dir,
            cfg_status_fname,
        )
    )

    def revert():
        logger.warning(
            f"An error occurred during update of github config '{cfg_to_rotate.name()}', "
            'rolling back'
        )
        for credential in technical_user_credentials:
            username = credential.username()
            logger.warning(f"Rolling back changes to user '{username}'")
            new_pub_key = new_public_keys[username]
            gh_api = ccc.github.github_api(cfg_to_rotate, username=username)
            for key in gh_api.keys():
                if key.key == new_pub_key:
                    key.delete()
                    logger.info(f"Rollback successful for key '{new_pub_key}'.")
            else:
                logger.warning(
                    f"New public key for '{username}' not known to github, nothing to revert."
                )

    return revert


def _write_github_configs(cfg_dir, cfg_file_name, github_configs):
    configs = {c.name(): c.raw for c in github_configs}
    with open(os.path.join(cfg_dir, cfg_file_name), 'w') as cfg_file:
        yaml.dump(configs, cfg_file, Dumper=ci.util.MultilineYamlDumper)


def update_user(
    github_config: GithubConfig,
    credential: GithubCredentials,
) -> typing.Tuple[str, str]:
    gh_api = ccc.github.github_api(github_config, username=credential.username())

    private_key, public_key = _create_key_pair()
    gh_api.create_key(
        title='Auto-generated key',
        key=public_key,
    )
    credential.raw['privateKey'] = private_key
    return public_key


def undo_update(
    github_config: GithubConfig,
    username: str,
):
    credential = github_config.credentials(technical_user_name=username)
    gh_api = ccc.github.github_api(github_config, username=username)
    private_key = credential.private_key()
    public_key = _corresponding_public_key(private_key)
    for key in gh_api.keys():
        if key.key == public_key:
            key.delete()
    else:
        logger.warning(
            f'Current public key for {username} not known to github, nothing to revert.'
        )


def delete_config_secret(
    cfg_factory: model.ConfigFactory,
    cfg_queue_entry: CfgQueueEntry,
) -> bool:
    github_config = cfg_factory.github(cfg_queue_entry.target.name)
    for entry in cfg_queue_entry.secretId['github_users']:
        username = entry['name']
        gh_api = ccc.github.github_api(github_config, username=username)
        for key in gh_api.keys():
            if key.key == entry['public_key']:
                key.delete()
            else:
                logger.warning(
                    f'Old public key for {username} not known to github, nothing to delete.'
                )

    return True


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

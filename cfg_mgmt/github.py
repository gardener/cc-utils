import dataclasses
import datetime
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
    CfgQueueEntry,
    CfgTarget,
    CfgStatus,
    CfgMetadata,
    cfg_queue_fname,
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
        _create_config_queue_entry(
            queue_entry_config_element=cfg_to_rotate,
            queue_entry_data={'github_users': old_public_keys},
        )
    )

    _write_config_queue(cfg_dir, cfg_metadata)

    _update_config_status(
        cfg_dir=cfg_dir,
        cfg_status_filename=cfg_status_fname,
        config_element=cfg_to_rotate,
        config_statuses=cfg_metadata.statuses,
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
        yaml.dump(configs, cfg_file, Dumper=MultilineYamlDumper)


def _write_config_queue(
    cfg_dir,
    cfg_metadata: CfgMetadata,
    queue_file_name=cfg_queue_fname,
):
    with open(os.path.join(cfg_dir, queue_file_name), 'w') as queue_file:
        yaml.dump(
            {
                'rotation_queue': [
                    dataclasses.asdict(cfg_queue_entry)
                    for cfg_queue_entry in cfg_metadata.queue
                ],
            },
            queue_file,
            Dumper=MultilineYamlDumper,
        )


def _update_config_status(
    cfg_dir: str,
    cfg_status_filename: str,
    config_element: model.NamedModelElement,
    config_statuses: typing.Iterable[CfgStatus],
):
    for cfg_status in config_statuses:
        if cfg_status.matches(
            element=config_element,
        ):
            break
    else:
        # does not exist
        cfg_status = CfgStatus(
            target=CfgTarget(
                type=config_element._type_name,
                name=config_element.name(),
            ),
            credential_update_timestamp=datetime.date.today().isoformat(),
        )
        config_statuses.append(cfg_status)
    cfg_status.credential_update_timestamp = datetime.date.today().isoformat()

    with open(os.path.join(cfg_dir, cfg_status_filename), 'w') as f:
        yaml.dump(
            {
                'config_status': [
                    dataclasses.asdict(cfg_status)
                    for cfg_status in config_statuses
                ]
            },
            f,
        )


def _create_config_queue_entry(
    queue_entry_config_element,
    queue_entry_data,
):
    return CfgQueueEntry(
        target=CfgTarget(
            name=queue_entry_config_element.name(),
            type=queue_entry_config_element._type_name,
        ),
        deleteAfter=(datetime.datetime.today() + datetime.timedelta(days=7)).date().isoformat(),
        secretId=queue_entry_data,
    )


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


class MultilineYamlDumper(yaml.SafeDumper):
    def represent_data(self, data):
        # by default, the SafeDumper includes an extra empty line for each line in the data for
        # string-blocks. As all provided ways to configure the dumper differently affect all
        # rendered types we create our own Dumper.
        if isinstance(data, str) and '\n' in data:
            return self.represent_scalar(u'tag:yaml.org,2002:str', data, style='|')
        # Also, don't include keys with None/null values.
        if data is None:
            return self.represent_scalar('tag:yaml.org,2002:null', '')
        return super().represent_data(data)

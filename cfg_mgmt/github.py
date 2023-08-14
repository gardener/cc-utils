import base64
import copy
import enum
import hashlib
import logging
import sys
import typing

import requests

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


def _rsa_sha256_fingerprint(key: str) -> str:
    # Returns the fingerprint (as shown in the GitHub UI).
    if not key.startswith('ssh-rsa '):
        return ''
    try:
        # fingerprint is built from the raw decoded public key, so we need to strip the prefix when
        # working with the string
        key = key.lstrip('ssh-rsa ').encode()
        key_raw = base64.b64decode(key)

        key_hash = hashlib.sha256(key_raw).digest()
        fingerprint = base64.b64encode(key_hash).decode()
        # remove '=' used for padding (they are not present in GitHub/ssh-keygen when showing
        # fingerprints)
        fingerprint = fingerprint.rstrip('=')
        return f'SHA256:{fingerprint}'
    except:
        # These fingerprints are intended to make our life easier when looking at the rotation
        # queue, they are not important enough to raise any errors.
        return ''


def _rotate_oauth_token(
    github_api_url: str,
    token_to_rotate: str,
) -> str | None:
    '''Rotate the token for the given credentials iff it is an oAuth token, as (poorly) determined
    by the `gho_` prefix.

    If this function is called for a token other than an oAuth token (e.g.: a personal access token)
    `None` is returned.
    The returned token has the same scopes as the initial token. If you'd like to update the scope
    of the token, you need to re-create it with additional scopes.

    Note: This will only work for oAuth tokens associated with 'git-credential-manager'
    (https://github.com/git-ecosystem/git-credential-manager) due to hardcoded client_id and
    client_secret.
    '''

    if not token_to_rotate.startswith('gho_'):
        logger.warning(
            f"oAuth token '{token_to_rotate}' does not start with 'gho_' - will not update."
        )
        return None
    # client_id and client_"secret" can be hardcoded here as they are the same on all github
    # instances. Also, they are hardcoded in the git credential manager sourcecode on github.com as
    # well, so no actual secrets are leaked here.
    # See https://github.com/git-ecosystem/git-credential-manager/
    # blob/f89105b1ce033e8c2756044d8b195eade2889dac/src/shared/GitHub/GitHubConstants.cs#L14-L17
    client_id = '0120e057bd645470c1ed'
    client_secret = '18867509d956965542b521a529a79bb883344c90'

    request_url = f'{github_api_url}/applications/{client_id}/token'
    request_kwargs = {
        'url': request_url,
        'auth': (client_id, client_secret), # basic auth
        'json': {'access_token': token_to_rotate},
    }
    # check token (to catch the case if the token not being associated with the git-credentials-
    # manager)
    resp = requests.post(**request_kwargs)
    if not resp.ok:
        # No way for the user to see the oAuth token that belongs to the app, but at least checking
        # whether the given token is currently associated with the app can be done.
        logger.warning(
            f"Given oAuth token '{token_to_rotate}' did not pass GitHub verification. Does the "
            'oAuth token belong to the git-credentials-manager application and is valid?'
        )
        return None
    # fire request that causes the actual refresh (i.e. "rotation") to happen.
    # Note: If successful, old token is immediately invalidated.
    resp = requests.patch(**request_kwargs)
    if not resp.ok:
        resp.raise_for_status()

    response_content = resp.json()

    return response_content['token']


def rotate_cfg_element(
    cfg_element: model.github.GithubConfig,
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
    old_user_data = []
    new_public_keys = dict()
    for i, credential in enumerate(technical_user_credentials):
        if not (private_key := credential.private_key()):
            logger.warn(f'{credential.username()=} did not have private-key - skipping')
            continue

        key_algorithm = _determine_key_algorithm(private_key)

        old_public_key = _corresponding_public_key(
            private_key=credential.private_key(),
            key_algorithm=key_algorithm,
        )

        old_key_fingerprint = _rsa_sha256_fingerprint(old_public_key)
        old_token = ''
        # first, try to update token (if possible, see _rotate_oauth_token())
        try:
            # We rotate by refreshing the secondary token and afterwards switching
            # the primary and secondary tokens. In effect, the old primary token would still be
            # valid until the next rotation happens, but we refresh the token when processing the
            # queue later.
            # This is done to avoid breaking running jobs, since existing tokens are invalidated
            # immediately upon refresh.
            token_to_rotate = credential.secondary_auth_token()
            if token_to_rotate:
                if new_token := _rotate_oauth_token(
                    github_api_url=cfg_to_rotate.api_url(),
                    token_to_rotate=token_to_rotate,
                ):
                    old_token = credential.auth_token()
                    credential.raw['secondary_authToken'] = old_token
                    credential.raw['authToken'] = new_token
                    credential.raw['password'] = new_token
            else:
                logger.info(
                    'No secondary oAuth token provided for credential with username '
                    f"'{credential.username}' of github-config '{cfg_to_rotate.name()}' "
                    '- will not attempt rotation.'
                )
        except:
            exception = sys.exception()
            if isinstance(exception, requests.exceptions.HTTPError):
                logger.error(
                    f'Error when trying to refresh oAuth token: {exception}. Response from server: '
                    f'{exception.response}'
                )
            else:
                logger.error(f'Error when trying to refresh oAuth token: {exception}')

            # we only abort here if the first rotation failed (assuming that the token was not
            # refreshed). Otherwise we keep on rotating keys/tokens to prevent the previously
            # succeded rotations from being lost due to the abort.
            if i == 0:
                raise
        config_queue_data = {
            'name': credential.username(),
            'public_key': old_public_key,
            'fingerprint': old_key_fingerprint,
        }
        if old_token:
            config_queue_data['oAuthToken'] = old_token

        old_user_data.append(config_queue_data)

        new_public_keys.update({
            credential.username(): update_user(
                github_config=cfg_to_rotate,
                credential=credential,
                cfg_factory=cfg_factory,
                key_algorithm=key_algorithm,
            )
        })

    secret_id = {'github_users': old_user_data}

    def revert():
        logger.warning(
            f"An error occurred during update of github config '{cfg_to_rotate.name()}', "
            'rolling back'
        )
        # No way to revert oAuth-token rotation. Roll back changes to ssh-keys and return
        for credential in technical_user_credentials:
            if not credential.private_key():
                logger.warn(f'{credential.username()=} did not have private-key - skipping')
                continue

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
) -> model.github.GithubConfig | None:
    raw_cfg = copy.deepcopy(cfg_element.raw)
    cfg_element = model.github.GithubConfig(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )
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
        if 'oAuthToken' in entry:
            for credential in cfg_element._technical_user_credentials():
                if credential.secondary_auth_token() == (token := entry['oAuthToken']):
                    credential.raw['secondary_authToken'] = _rotate_oauth_token(
                        github_api_url=cfg_element.api_url(),
                        token_to_rotate=token,
                    )
    return cfg_element


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

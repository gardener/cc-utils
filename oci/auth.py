import base64
import collections.abc
import dataclasses
import enum
import json
import operator
import os

import oci.util


class AuthType(enum.Enum):
    BASIC_AUTH = 'basic_auth'


class Privileges(enum.Enum):
    READONLY = 'readonly'
    READWRITE = 'readwrite'
    ADMIN = 'admin'

    def _asint(self, privileges):
        if privileges is self.READONLY:
            return 0
        elif privileges is self.READWRITE:
            return 1
        elif privileges is self.ADMIN:
            return 2
        elif privileges is None:
            return 4
        else:
            raise NotImplementedError(privileges)

    def __hash__(self):
        return self._asint(self).__hash__()

    def __lt__(self, other):
        o = self._asint(other)
        return self._asint(self).__lt__(o)

    def __le__(self, other):
        o = self._asint(other)
        return self._asint(self).__le__(o)

    def __eq__(self, other):
        o = self._asint(other)
        return self._asint(self).__eq__(o)

    def __ne__(self, other):
        o = self._asint(other)
        return self._asint(self).__ne__(o)

    def __gt__(self, other):
        o = self._asint(other)
        return self._asint(self).__gt__(o)

    def __ge__(self, other):
        o = self._asint(other)
        return self._asint(self).__ge__(o)


@dataclasses.dataclass(frozen=True)
class OciCredentials:
    pass


@dataclasses.dataclass(frozen=True)
class OciConfig:
    privileges: Privileges
    credentials: OciCredentials
    url_prefixes: collections.abc.Sequence[str] = dataclasses.field(default_factory=tuple)

    def valid_for(self, image_reference: str, privileges: Privileges=Privileges.READONLY):
        if privileges and privileges > self.privileges:
            return False

        if not self.url_prefixes:
            return True

        unmodified_ref = image_reference.lower()
        image_reference = oci.util.normalise_image_reference(image_reference=image_reference).lower()

        for prefix in self.url_prefixes:
            prefix = prefix.lower()

            if image_reference.startswith(oci.util.normalise_image_reference(prefix)):
                return True
            if image_reference.startswith(prefix.lower()):
                return True
            if unmodified_ref.startswith(prefix):
                return True

        return False


@dataclasses.dataclass(frozen=True)
class OciBasicAuthCredentials(OciCredentials):
    username: str
    password: str


# typehint-alias
image_reference = str
credentials_lookup = collections.abc.Callable[[image_reference, Privileges, bool], OciCredentials]


def mk_credentials_lookup(
    cfgs: OciCredentials | collections.abc.Sequence[OciCredentials],
) -> collections.abc.Callable[[image_reference, Privileges, bool], OciConfig]:
    '''
    returns a callable that can be queried for matching OciCredentials for requested
    privileges and image-references
    '''
    if isinstance(cfgs, OciConfig):
        cfgs = (cfgs,)

    def lookup_credentials(
        image_reference: str,
        privileges: Privileges=Privileges.READONLY,
        absent_ok: bool=False,
    ):
        valid_cfgs = sorted(
          (
            c for c in cfgs
            if c.valid_for(image_reference=image_reference, privileges=privileges)
          ),
          key=operator.attrgetter('privileges'),
        )

        if not valid_cfgs and absent_ok:
            return None

        if not valid_cfgs:
            raise ValueError(f'no valid cfg found: {image_reference=}, {privileges=}')

        # first element contains cfg with least required privileges
        return valid_cfgs[0].credentials

    return lookup_credentials


def docker_credentials_lookup(
    docker_cfg: str | None=None,
    absent_ok: bool=False,
) -> collections.abc.Callable[[image_reference, Privileges, bool], OciConfig]:
    '''
    returns a credentials-lookup backed by docker's auth-config. By design, docker's auth-config
    only allows configuring credentials per hostname. By default' docker-cfg is expected at
    `$HOME/.docker/config.json`. Location of docker-cfg can be customised via docker_cfg parameter.

    if no docker-cfg is found, raises RuntimeError, unless absent_ok is truthy, in which case the
    returned lookup will never return any credentials (which might still be useful for readonly
    operations that for many registries allow anonymous access).

    Note that docker does not offer to configure credentials by permissions, too (hence privileges
    parameter will be ignored)
    '''
    if not docker_cfg:
        docker_cfg = os.path.join(os.environ.get('HOME', ''), '.docker/config.json')

    if not os.path.isfile(docker_cfg):
        if not absent_ok:
            raise RuntimeError(f'not an existing file: {docker_cfg=}')

        def find_nothing_lookup(
            image_reference: str,
            privileges: Privileges=Privileges.READONLY,
            absent_ok: bool=False,
        ):
            if not absent_ok:
                raise ValueError(f'no auth-cfg found in {docker_cfg=} for {image_reference=}')
            return None

        return find_nothing_lookup

    def docker_auth_lookup(
        image_reference: str,
        privileges: Privileges=Privileges.READONLY,
        absent_ok: bool=False,
    ):
        # re-read docker-cfg to reflect fs-updates
        with open(docker_cfg) as f:
            docker_auth = json.load(f)
            auths = docker_auth.get('auths', None)

        if not auths:
            # docker-cfg might be empty - do not handle as an error; however, we can never serve
            # anything useful
            if not absent_ok:
                raise ValueError(f'no auth-cfg found in {docker_cfg=} for {image_reference=}')
            return None

        if image_reference.startswith('/'):
            # if it is a relative reference, we have not means to find appropriate cfg.
            if not absent_ok:
                raise ValueError(f'no auth-cfg found in {docker_cfg=} for {image_reference=}')
            return None

        # ignore ports - match cfg only by hostname
        image_netloc = image_reference.split('/')[0]
        image_host = image_netloc.split(':')[0]

        for netloc, auth_dict in auths.items():
            host = netloc.split(':')[0]
            if host == image_host:
                break
        else:
            if not absent_ok:
                raise ValueError(
                    f'no matching auth-cfg found in {docker_cfg=} for {image_reference=}'
                )
            return None # no matching cfg was found

        # we found a cfg
        # docker's auth-cfgs only have a single value `auth` (or so we hope / assume)
        auth = auth_dict.get('auth', None)
        if not auth:
            raise ValueError(f'did not find expected attr `auth` in {docker_cfg=} for {image_host=}')

        auth = base64.b64decode(auth).decode('utf-8')
        username, passwd = auth.split(':', 1)

        return OciBasicAuthCredentials(
            username=username,
            password=passwd,
        )

    return docker_auth_lookup

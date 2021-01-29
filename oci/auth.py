import dataclasses
import enum
import operator
import typing

import oci.util


class AuthType(enum.Enum):
    BASIC_AUTH = 'basic_auth'


class Privileges(enum.Enum):
    READONLY = 'readonly'
    READWRITE = 'readwrite'

    def _asint(self, privileges):
        if privileges is self.READONLY:
            return 0
        elif privileges is self.READWRITE:
            return 1
        elif privileges is None:
            return 2
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
    url_prefixes: typing.Sequence[str] = dataclasses.field(default_factory=tuple)

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
credentials_lookup = typing.Callable[[image_reference, Privileges, bool], OciCredentials]


def mk_credentials_lookup(
    cfgs: typing.Union[OciCredentials, typing.Sequence[OciCredentials]],
) -> typing.Callable[[image_reference, Privileges, bool], OciConfig]:
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

'''
This module comprises model classes to define JSON Web Keys according
to https://datatracker.ietf.org/doc/html/rfc7518 as well as convenience
functions to it.
'''
import base64
import dataclasses
import datetime
import enum
import functools
import logging
import typing

import Crypto.PublicKey.RSA
import Crypto.Util.number
import dacite
import jwt

import model.delivery

logger = logging.getLogger(__name__)
JWT_KEY = 'bearer_token'


class KeyType(enum.StrEnum):
    RSA = 'RSA'
    OCTET_SEQUENCE = 'oct' # used to represent symmetric keys


class Use(enum.StrEnum):
    SIGNATURE = 'sig'
    ENCRYPTION = 'enc'


class Algorithm(enum.StrEnum):
    RS256 = 'RS256'
    HS256 = 'HS256'


@dataclasses.dataclass(frozen=True)
class JSONWebKey:
    kty: KeyType
    use: Use
    alg: Algorithm
    kid: str

    @property
    def key(self) -> bytes:
        # has to be defined by derived classes
        raise NotImplementedError

    @staticmethod
    def from_dict(data: dict) -> typing.Self:
        algorithm = Algorithm(data.get('alg'))

        class_by_algorithm = {
            Algorithm.RS256: RSAPublicKey,
            Algorithm.HS256: SymmetricKey,
        }

        return dacite.from_dict(
            data_class=class_by_algorithm.get(algorithm),
            data=data,
            config=dacite.Config(
                cast=[enum.Enum],
            ),
        )

    @staticmethod
    def from_signing_cfg(signing_cfg: model.delivery.SigningCfg) -> typing.Self:
        algorithm = Algorithm(signing_cfg.algorithm().upper())
        use = Use.SIGNATURE
        kid = signing_cfg.id()

        if algorithm == Algorithm.RS256:
            public_key = Crypto.PublicKey.RSA.import_key(signing_cfg.public_key())

            return RSAPublicKey(
                use=use,
                kid=kid,
                n=encodeBase64urlUInt(public_key.n),
                e=encodeBase64urlUInt(public_key.e),
            )
        elif algorithm == Algorithm.HS256:
            return SymmetricKey(
                use=use,
                kid=kid,
                k=encodeBase64url(signing_cfg.secret().encode('utf-8')),
            )


@dataclasses.dataclass(frozen=True, kw_only=True)
class RSAPublicKey(JSONWebKey):
    n: str # modulus (Base64urlUInt encoded)
    e: str # exponent (Base64urlUInt encoded)
    kty: KeyType = KeyType.RSA
    alg: Algorithm = Algorithm.RS256

    @property
    def key(self) -> bytes:
        return Crypto.PublicKey.RSA.construct(
            rsa_components=(
                decodeBase64urlUInt(self.n),
                decodeBase64urlUInt(self.e),
            ),
        ).export_key(format='PEM')


@dataclasses.dataclass(frozen=True, kw_only=True)
class SymmetricKey(JSONWebKey):
    k: str # key value (base64url encoded)
    kty: KeyType = KeyType.OCTET_SEQUENCE
    alg: Algorithm = Algorithm.HS256

    @property
    def key(self) -> bytes:
        return decodeBase64url(self.k)


def encodeBase64url(b: bytes) -> str:
    url_encoding = base64.urlsafe_b64encode(b)

    return url_encoding.rstrip(b'=').decode('utf-8')


def decodeBase64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '==')


def encodeBase64urlUInt(number: int) -> str:
    b = Crypto.Util.number.long_to_bytes(number)

    return encodeBase64url(b)


def decodeBase64urlUInt(s: str) -> int:
    b = decodeBase64url(s)

    return Crypto.Util.number.bytes_to_long(b)


@functools.cache
def decode_jwt(
    token: str,
    verify_signature: bool=True,
    json_web_key: JSONWebKey | None=None,
    **kwargs,
) -> dict:
    '''
    This is just a convenience wrapper for `jwt.decode` which eases
    signature validation of a given `token` using a `JSONWebKey`.
    '''
    if verify_signature and not json_web_key:
        raise ValueError('`json_web_key` must be specified if `verify_signature` is `True`')

    return jwt.decode(
        jwt=token,
        key=json_web_key.key if json_web_key else None,
        algorithms=[json_web_key.alg if json_web_key else None],
        options={
            'verify_signature': verify_signature,
        },
        **kwargs,
    )


def is_jwt_token_expired(
    token: str,
) -> bool:
    decoded_jwt = decode_jwt(
        token=token,
        verify_signature=False,
    )

    expiration_date = datetime.datetime.fromtimestamp(
        timestamp=decoded_jwt.get('exp'),
        tz=datetime.timezone.utc,
    )

    now = datetime.datetime.now(tz=datetime.timezone.utc)

    return now > expiration_date

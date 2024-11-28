'''
A client for Signing-Server
'''

import dataclasses
import hashlib
import io
import logging
import re
import urllib.parse

import cryptography.x509
import cryptography.hazmat.primitives.serialization as crypto_serialiation
import requests
import urllib3

import ci.util
import model.signing_server as ms


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class SigningserverClientCfg:
    base_url: str
    client_certificate: str
    client_certificate_key: str
    server_certificate_ca: str
    validate_tls_certificate: bool = True


@dataclasses.dataclass
class SigningResponse:
    '''
    wrapper for response received for signing-request to signing-server

    Instances are typically created from SigningserverClient.
    '''
    raw: str
    signing_algorithm: ms.SigningAlgorithm

    @property
    def certificate(self) -> str:
        '''
        returns certificate (may be used to extract public key for signature validation)
        certificate will be returned in PEM format
        '''
        start_idx = self.raw.find('-----BEGIN CERTIFICATE-----')
        end_str = '-----END CERTIFICATE-----'
        end_idx = self.raw.find(end_str)

        return self.raw[start_idx:end_idx + len(end_str)]

    @property
    def signature(self) -> str:
        '''
        returns signature (without header/footer) as base64 str
        '''
        start_str = '-----BEGIN SIGNATURE-----'
        start_idx = self.raw.find(start_str)
        end_str = '-----END SIGNATURE-----'
        end_idx = self.raw.find(end_str)

        signature = self.raw[start_idx + len(start_str):end_idx].strip() # strip header and footer
        # strip pre-ambel (Signature Algorithm: <alg>) + blank line
        start_idx = signature.find('\n')
        signature = signature[start_idx + 1:]

        return signature.strip()

    @property
    def public_key(self) -> str:
        '''
        returns the PEM-encoded public-key corresponding to the private key used for creating
        thus response's signature.
        '''
        certificate = cryptography.x509.load_pem_x509_certificate(
            self.raw.encode('utf-8')
        )
        public_key = certificate.public_key()
        public_key_str = public_key.public_bytes(
            encoding=crypto_serialiation.Encoding.PEM,
            format=crypto_serialiation.PublicFormat.SubjectPublicKeyInfo,
        ).decode('utf-8')

        return public_key_str


class SigningserverException(Exception):
    pass


class SigningserverClient:
    def __init__(
        self,
        cfg: SigningserverClientCfg,
    ):
        self.cfg = cfg

    def sign(
        self,
        content: str | bytes | io.IOBase=None,
        digest: str | bytes=None,
        hash_algorithm='sha256',
        signing_algorithm: ms.SigningAlgorithm | str = ms.SigningAlgorithm.RSASSA_PSS,
        remaining_retries: int=3,
    ):
        if not (bool(content) ^ bool(digest)):
            raise ValueError('exactly one of `content` or `digest` must be passed')

        signing_algorithm = ms.SigningAlgorithm(signing_algorithm)
        url = ci.util.urljoin(
            self.cfg.base_url,
            'sign',
            signing_algorithm,
        ) + '?' + urllib.parse.urlencode({'hashAlgorithm': hash_algorithm})

        if content:
            hasher = getattr(hashlib, hash_algorithm, None)
            if not hasher:
                raise ValueError(hash_algorithm)

            digest = hasher(content).digest()
        elif isinstance(digest, str):
            digest = bytes.fromhex(digest)

        kwargs = {}
        if self.cfg.server_certificate_ca:
            kwargs['verify'] = self.cfg.server_certificate_ca

        if self.cfg.validate_tls_certificate is False:
            kwargs['verify'] = False
            urllib3.disable_warnings()

        try:
            resp = requests.post(
                url=url,
                headers={
                    'Accept': 'application/x-pem-file',
                },
                data=digest,
                timeout=(4, 31),
                cert=(self.cfg.client_certificate, self.cfg.client_certificate_key,),
                **kwargs,
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if remaining_retries == 0:
                raise SigningserverException(e)

            logger.warning(f'caught http error, going to retry... ({remaining_retries=}); {e}')
            return self.sign(
                content=content,
                hash_algorithm=hash_algorithm,
                signing_algorithm=signing_algorithm,
                remaining_retries=remaining_retries - 1,
            )
        except Exception as e:
            raise SigningserverException(e)

        return SigningResponse(
            raw=resp.text,
            signing_algorithm=signing_algorithm,
        )


@dataclasses.dataclass
class DistinguishedName:
    country: str | None = None
    state: str | None = None
    locality: str | None = None
    organization: str | None = None
    organizational_unit: str | None = None
    common_name: str | None = None

    @staticmethod
    def from_dict(distinguished_name: dict[str, str]) -> 'DistinguishedName':
        for key in distinguished_name.keys():
            allowed_attributes = ['C', 'ST', 'L', 'O', 'OU', 'CN']
            if key not in allowed_attributes:
                raise ValueError(
                    f'"{key}" is not valid as distinguished name ({allowed_attributes=})'
                )

        return DistinguishedName(
            country=distinguished_name.get('C'),
            state=distinguished_name.get('ST'),
            locality=distinguished_name.get('L'),
            organization=distinguished_name.get('O'),
            organizational_unit=distinguished_name.get('OU'),
            common_name=distinguished_name.get('CN'),
        )

    @staticmethod
    def parse(name: str) -> 'DistinguishedName':
        name = name.strip()

        if not name:
            raise ValueError(f'{name=} must not be empty')

        # regexes taken from https://github.com/open-component-model/ocm/blob/1b31de5579a0cc3dd49adba3a1df8ca1a7e7bb35/api/tech/signing/signutils/names.go#L17 # noqa: E501
        isDNRegex = r'^[^=]+=[^/;,+]+([/;,+][^=]+=[^/;,+]+)*$'
        dnGroupsRegex = r'[/;,+]([^=]+)=([^/;,+]+)'

        if not re.match(isDNRegex, name):
            # name does not match general DN regex -> interpret as common name by default
            return DistinguishedName(common_name=name)

        distinguished_name = {}
        for match in re.findall(dnGroupsRegex, f'+{name}'):
            distinguished_name[match[0].strip()] = match[1].strip()

        return DistinguishedName.from_dict(distinguished_name)

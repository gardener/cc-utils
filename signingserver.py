'''
A client for Signing-Server
'''

import dataclasses
import enum
import hashlib
import io
import urllib.parse

import cryptography.x509
import cryptography.hazmat.primitives.serialization as crypto_serialiation
import requests
import urllib3

import ci.util


class SigningAlgorithm(enum.StrEnum):
    RSASSA_PSS = 'rsassa-pss'
    RSASSA_PKCS1_V1_5 = 'rsassa-pkcs1-v1_5'


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
    signing_algorithm: SigningAlgorithm

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


class SigningserverClient:
    def __init__(
        self,
        cfg: SigningserverClientCfg,
    ):
        self.cfg = cfg

    def sign(
        self,
        content: str | bytes | io.IOBase,
        hash_algorithm='sha256',
        signing_algorithm: SigningAlgorithm | str = SigningAlgorithm.RSASSA_PSS,
    ):
        signing_algorithm = SigningAlgorithm(signing_algorithm)
        url = ci.util.urljoin(
            self.cfg.base_url,
            'sign',
            signing_algorithm,
        ) + '?' + urllib.parse.urlencode({'hashAlgorithm': hash_algorithm})

        hasher = getattr(hashlib, hash_algorithm, None)
        if not hasher:
            raise ValueError(hash_algorithm)

        digest = hasher(content).digest()

        kwargs = {}
        if self.cfg.server_certificate_ca:
            kwargs['verify'] = self.cfg.server_certificate_ca

        if self.cfg.validate_tls_certificate is False:
            kwargs['verify'] = False
            urllib3.disable_warnings()

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

        return SigningResponse(
            raw=resp.text,
            signing_algorithm=signing_algorithm,
        )

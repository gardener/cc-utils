import copy
import logging
import typing
import requests
import tempfile
import os
from contextlib import contextmanager
from dataclasses import dataclass
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import pkcs7

import cfg_mgmt
import cfg_mgmt.model as cmm
import ci.log
import ci.util
import model
import model.container_registry
from model.btp_application_certificate import BtpApplicationCertificate


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


class CertServiceClient:
    def __init__(
        self,
        credentials: dict,
    ):
        self.credentials = credentials
        self._setup_oauth_token()

    def _setup_oauth_token(self):
        uaa = self.credentials['uaa']
        data = {
            'grant_type': 'client_credentials',
            'token_format': 'bearer',
            'client_id': uaa['clientid'],
            'client_secret': uaa['clientsecret'],
        }
        headers = {
            'Accept': 'application/json',
        }
        resp = requests.post(f'{uaa["url"]}/oauth/token', data=data, headers=headers)
        resp.raise_for_status()
        self.access_token = resp.json()['access_token']

    def create_client_certificate_chain(self, csr_pem: str, validity_in_days: int) -> dict:
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.access_token}',
        }
        data = {'certificate-signing-request':{
            'value': csr_pem,
            'type': 'pkcs10-pem',
            'validity': {'type': 'DAYS', 'value': validity_in_days},
        }}
        url = self.credentials['certificateservice']['profileurl']
        resp = requests.post(url, json=data, headers=headers)
        resp.raise_for_status()
        logger.info('Created certificate')
        return resp.json()['certificate-response']


def _write_temp_file(temp_dir: str, fname: str, content: str) -> str:
    fpath = os.path.join(temp_dir, fname)
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(content)
    return fpath


@dataclass
class CertInfo:
    id: str
    dn: str
    cn: str
    serial_no: int


class GBaasAppClient:
    def __init__(self, auth: BtpApplicationCertificate):
        endpoint = auth.application_endpoint()
        self.url = f'{endpoint}/service/sps/{auth.application_id()}/apiCertificate'
        self.clienturl = f'{endpoint}/service/sps/{auth.client_id()}/apiCertificate'
        self.certificate_pem = auth.certificate_pem()
        self.private_key_pem = auth.private_key_pem()

    @contextmanager
    def _session_with_cert(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = requests.Session()
            crt_fname = _write_temp_file(temp_dir, 'cert.pem', self.certificate_pem)
            key_fname = _write_temp_file(temp_dir, 'key.pem', self.private_key_pem)
            session.cert = (crt_fname, key_fname)
            yield session

    def put_certificate(self, cert_pem: str, desc: str, scopes: list[str]) -> str:
        cert_pem = cert_pem.strip()
        cert_pem = cert_pem.removeprefix("-----BEGIN CERTIFICATE-----\n")
        cert_pem = cert_pem.removesuffix("-----END CERTIFICATE-----")
        data = {
            'description': desc,
            'scopes': scopes,
            'base64': cert_pem,
        }
        with self._session_with_cert() as session:
            resp = session.put(self.url, json=data)
        resp.raise_for_status()
        id = resp.json()['certificateId']
        logger.info(f'Added certificate {id}')
        return id

    def delete_certificate(self, common_name: str, cert_id: str):
        data = {
            'certificateId': cert_id,
        }
        with self._session_with_cert() as session:
            resp = session.delete(self.url, json=data)
        resp.raise_for_status()
        logger.info(f'Deleted certificate {common_name} ({cert_id})')

    @staticmethod
    def _find_cn(dn: str) -> str:
        for part in dn.split(','):
            key, value = part.split('=')
            if key == 'CN':
                return value
        return None

    def list_certificates_by_base(
        self,
        common_name_base: str
    ) -> typing.Generator[CertInfo, None, None]:
        with self._session_with_cert() as session:
            resp = session.get(self.clienturl)
        resp.raise_for_status()
        for item in resp.json():
            id = item['dnId']
            dn = item['dn']
            cn = GBaasAppClient._find_cn(dn)
            if cn:
                try:
                    serial_no, base = BtpApplicationCertificate.parse_serial_no_from_common_name(cn)
                    if common_name_base == base:
                        yield CertInfo(id=id, dn=dn, cn=cn, serial_no=serial_no)
                except ValueError:
                    pass


_str_to_names = {
    'C': NameOID.COUNTRY_NAME,
    'ST': NameOID.STATE_OR_PROVINCE_NAME,
    'O': NameOID.ORGANIZATION_NAME,
    'OU': NameOID.ORGANIZATIONAL_UNIT_NAME,
    'L': NameOID.LOCALITY_NAME,
    'CN': NameOID.COMMON_NAME,
    'EMAIL': NameOID.EMAIL_ADDRESS,
}


def _create_csr(subject: str) -> tuple[str, str]:
    logger.info(f'Creating CSR for {subject}')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode('utf-8')
    attributes = []
    for part in subject.split(', '):
        name, value = part.split('=')
        oid = _str_to_names[name]
        attributes.append(x509.NameAttribute(oid, value))
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name(attributes)
    ).sign(key, hashes.SHA256())
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode('utf-8')
    return csr_pem, key_pem


def _extract_client_certificate(cert_response: dict) -> str:
    pkcs7_pem = cert_response['value']
    certs = pkcs7.load_pem_pkcs7_certificates(pkcs7_pem.encode('utf-8'))
    if not certs:
        raise ValueError('no certificates found in response')
    return certs[0].public_bytes(serialization.Encoding.PEM).decode('utf-8')


def rotate_cfg_element(
    cfg_element: BtpApplicationCertificate,
    cfg_factory: model.ConfigFactory,
) -> typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:
    gbaas_auth = cfg_factory.btp_application_certificate(cfg_element.auth_application_certificate())
    gbaas_client = GBaasAppClient(gbaas_auth)

    # calc next serial no
    cn = cfg_element.common_name()
    serial_no, base = BtpApplicationCertificate.parse_serial_no_from_common_name(cn)
    next_sn = serial_no + 1
    for info in gbaas_client.list_certificates_by_base(base):
        if info.serial_no >= next_sn:
            next_sn = info.serial_no + 1
    next_cn = f'{next_sn}.{base}'

    # create certificate
    csr_pem, key_pem = _create_csr(cfg_element.subject(next_cn))
    sb_auth = cfg_factory.btp_service_binding(cfg_element.cert_service_binding())
    cs_client = CertServiceClient(sb_auth.credentials())
    response = cs_client.create_client_certificate_chain(csr_pem, cfg_element.validity_in_days())
    cert_pem = _extract_client_certificate(response)

    # add certificate to GBaas application
    id = gbaas_client.put_certificate(
        cert_pem=cert_pem,
        desc=f'CN={next_cn}',
        scopes=cfg_element.scopes(),
    )

    secret_id = {'common_name': cn}
    raw_cfg = copy.deepcopy(cfg_element.raw)
    raw_cfg['certificate_pem'] = cert_pem
    raw_cfg['private_key_pem'] = key_pem
    raw_cfg['common_name'] = next_cn
    updated_elem = BtpApplicationCertificate(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )

    def revert():
        gbaas_client.delete_certificate(next_cn, id)

    return revert, secret_id, updated_elem


def delete_config_secret(
    cfg_element: BtpApplicationCertificate,
    cfg_queue_entry: cmm.CfgQueueEntry,
    cfg_factory: model.ConfigFactory,
):
    logger.info('Deleting old certificates')
    gbaas_auth = cfg_factory.btp_application_certificate(cfg_element.auth_application_certificate())
    gbaas_client = GBaasAppClient(gbaas_auth)
    cn = cfg_queue_entry.secretId['common_name']
    serial_no, base = BtpApplicationCertificate.parse_serial_no_from_common_name(cn)
    for info in gbaas_client.list_certificates_by_base(base):
        if info.serial_no < serial_no:
            gbaas_client.delete_certificate(info.cn, info.id)

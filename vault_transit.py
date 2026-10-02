# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

"""
A client for signing via HashiCorp Vault's Transit secrets engine.

This is a sibling backend to `signingserver.SigningserverClient`: it produces the same
consumed surface (`.signature`, `.public_key`, `.signing_algorithm`, `.raw`) so that callers
(e.g. cosign signature-creation and OCM component-descriptor signing) can switch between the
signing-server and Vault Transit without any change to how the resulting signature is packaged.

This client never performs a login. It consumes a Vault token (plus base-url and
optional namespace) supplied as plain config fields on `VaultTransitClientCfg`; how the caller
obtains and populates those values is entirely up to the caller.

Vault Transit returns signatures as `vault:v<version>:<base64>`. Verifiers (cosign, and the OCM
`validate` command) expect the raw base64 signature, so the `vault:v<n>:` prefix is stripped.

The Vault Transit backend is a complete drop-in replacement for the signing-server backend,
and requires a certificate chain to be attached to the signing Vault Transit key.

Setting up a Vault Transit key and attaching a certificate chain can be accomplished by the
following steps.

  # 1. Enable the transit secrets engine (if not already enabled)
  $ vault secrets enable transit

  # 2. Create an asymmetric signing key, e.g.
  $ vault write transit/keys/<key> type=rsa-3072

  # 3. Generate a CSR for the Vault Transit key and optionally refine the generated CSR
  $ vault write transit/keys/<key>/csr csr=@<existing.csr>

  # 4. Provide the CSR to your CA, receive leaf (+ intermediates), concatenate leaf-first

  # 5. Attach the certificate chain to your Vault Transit key (leaf first)
  $ vault write transit/keys/<key>/set-certificate certificate_chain=@<leaf-then-intermediates.pem>
"""

import base64
import dataclasses
import hashlib
import io
import logging
import time

import hvac

from signingserver import SigningAlgorithm


logger = logging.getLogger(__name__)

# Maps our SigningAlgorithm to the `signature_algorithm` value understood by Vault Transit
_vault_signature_algorithm = {
    SigningAlgorithm.RSASSA_PSS: 'pss',
    SigningAlgorithm.RSASSA_PKCS1_V1_5: 'pkcs1v15',
}

# Maps a hashlib-style hash-algorithm name to the `hash_algorithm` value understood by Vault Transit
_vault_hash_algorithm = {
    'sha224': 'sha2-224',
    'sha256': 'sha2-256',
    'sha384': 'sha2-384',
    'sha512': 'sha2-512',
}


class VaultTransitException(Exception):
    """
    A generic exception raised by the Vault Transit backend.
    """
    pass


@dataclasses.dataclass
class VaultTransitClientCfg:
    """
    Represents the configuration settings for a Vault Transit client.
    """
    base_url: str
    token: str
    engine: str  # Transit engine mount-path (e.g. `transit`)
    key: str  # Name of the transit key to sign with
    namespace: str | None = None
    validate_tls_certificate: bool = True
    connect_timeout: int = 12


@dataclasses.dataclass
class VaultTransitSigningResponse:
    """
    Wrapper for a signature created via Vault Transit.

    Mirrors the consumed surface of `signingserver.SigningResponse` so both backends are
    interchangeable at call-sites.
    """
    signature: str  # Raw base64 signature (Vault's `vault:v<n>:` prefix already stripped)
    public_key: str  # PEM-encoded public-key of the configured transit key (as returned by Vault)
    public_key_version: str  # Version of the public key used to sign the signature
    signing_algorithm: SigningAlgorithm
    certificate_chain: str  # PEM-encoded certificate chain (leaf-first) of the signing key version.

    @property
    def raw(self) -> str:
        """
        Returns the certificate chain followed by the base64 signature wrapped in a
        `-----BEGIN SIGNATURE-----` ... `-----END SIGNATURE-----` PEM envelope with a
        `Signature Algorithm:` header.

        This matches the format the signing-server backend emits (one or more leaf-first
        `-----BEGIN CERTIFICATE-----` blocks, then the SIGNATURE block), so the OCM
        component-descriptor signing/verification path consumes a vault-produced signature
        identically. A certificate chain is always present.
        """
        algorithm = SigningAlgorithm.as_rfc_standard(self.signing_algorithm)

        # Emit the chain verbatim (leaf-first), ensuring exactly one trailing newline before the
        # SIGNATURE block
        certificate_block = self.certificate_chain.strip() + '\n'

        return (
            f'{certificate_block}'
            '-----BEGIN SIGNATURE-----\n'
            f'Signature Algorithm: {algorithm}\n'
            '\n'
            f'{self.signature}\n'
            '-----END SIGNATURE-----\n'
        )


class VaultTransitClient:
    """
    Client for the Vault Transit secrets engine.
    """
    def __init__(
        self,
        cfg: VaultTransitClientCfg,
    ):
        self.cfg = cfg
        self._client = hvac.Client(
            url=cfg.base_url,
            token=cfg.token,
            namespace=cfg.namespace,
            verify=cfg.validate_tls_certificate,
            timeout=cfg.connect_timeout,
        )

    def _read_key_version(self, version: str) -> tuple[str, str | None]:
        """
        Reads the configured transit key and returns a `(public_key, certificate_chain)` tuple for
        the given version. `public_key` is PEM-encoded; `certificate_chain` is the PEM chain
        (leaf-first) attached via the `set-certificate` Vault API, or `None` if no
        chain is attached.
        """
        # Allow specifying a v<N> prefix, but strip it away here, since the `v`
        # prefix is not present in the key's data.
        version = version.removeprefix('v')

        try:
            resp = self._client.secrets.transit.read_key(
                name=self.cfg.key,
                mount_point=self.cfg.engine,
            )
        except Exception as e:
            raise VaultTransitException(f'failed to read transit key {self.cfg.key!r}') from e

        data = resp['data']
        if version not in data.get('keys', {}):
            raise VaultTransitException(f'public key version {version} does not exist for {self.cfg.key!r}')

        key_version = data['keys'][version]

        public_key = key_version.get('public_key')
        if not public_key:
            raise VaultTransitException(
                f'transit key {self.cfg.key!r} (version {version}) has no public_key - '
                'is it an asymmetric (rsa-*/ecdsa-*/ed25519) key?'
            )

        # `certificate_chain` is only present if a chain was attached via `set-certificate`.
        certificate_chain = key_version.get('certificate_chain')
        if not (certificate_chain and certificate_chain.strip()):
            certificate_chain = None

        return public_key, certificate_chain

    def public_key_version(self, version: str) -> str:
        """
        Returns the PEM-encoded public-key version of the configured transit key.
        """
        public_key, _ = self._read_key_version(version)

        return public_key

    def sign(
        self,
        content: str | bytes | io.IOBase | None = None,
        digest: str | bytes | None = None,
        hash_algorithm: str = 'sha256',
        signing_algorithm: SigningAlgorithm | str = SigningAlgorithm.RSASSA_PSS,
        remaining_retries: int = 3,
    ) -> VaultTransitSigningResponse:
        # Signature of this method deliberately mirrors `signingserver.SigningserverClient.sign`
        if not (bool(content) ^ bool(digest)):
            raise ValueError('exactly one of `content` or `digest` must be passed')

        signing_algorithm = SigningAlgorithm(signing_algorithm)

        vault_signature_algorithm = _vault_signature_algorithm.get(signing_algorithm)
        if not vault_signature_algorithm:
            raise ValueError(f'unsupported {signing_algorithm=} for vault-transit')

        vault_hash_algorithm = _vault_hash_algorithm.get(hash_algorithm)
        if not vault_hash_algorithm:
            raise ValueError(f'unsupported {hash_algorithm=} for vault-transit')

        # Hash `content` locally, exactly like signingserver does, so that both backends sign the
        # same prehashed digest (Vault is told `prehashed=True`).
        if content:
            hasher = getattr(hashlib, hash_algorithm, None)
            if not hasher:
                raise ValueError(hash_algorithm)
            digest = hasher(content).digest()
        elif isinstance(digest, str):
            digest = bytes.fromhex(digest)

        assert isinstance(digest, bytes)

        try:
            resp = self._client.secrets.transit.sign_data(
                name=self.cfg.key,
                mount_point=self.cfg.engine,
                hash_input=base64.b64encode(digest).decode('utf-8'),
                prehashed=True,
                hash_algorithm=vault_hash_algorithm,
                signature_algorithm=vault_signature_algorithm,
                # Match the OCM verifier which expects PSS.MAX_LENGTH
                # salt. `salt_length='auto'` instructs Vault to use the largest
                # salt possible, which is ignored for pkcs1v15.
                salt_length='auto' if signing_algorithm is SigningAlgorithm.RSASSA_PSS else None,
            )
        except Exception as e:
            if remaining_retries == 0:
                raise VaultTransitException(e)

            logger.warning(f'caught error signing via vault, going to retry... ({remaining_retries=}); {e}') # noqa: E501
            time.sleep(2 ** (3 - remaining_retries))
            return self.sign(
                digest=digest,
                hash_algorithm=hash_algorithm,
                signing_algorithm=signing_algorithm,
                remaining_retries=remaining_retries - 1,
            )

        # Parse the vault signature (`vault:v<n>:<base64>`).
        vault_signature = resp['data']['signature']
        vault_signature_parts = vault_signature.split(':')
        if len(vault_signature_parts) != 3:
            raise VaultTransitException(f'unexpected vault signature format (expected 3 colon-separated parts, got {len(vault_signature_parts)})')

        # Signature is `vault:v<n>:<base64>`; the version and the raw base64 (as expected by
        # cosign / ocm-verify) are simply the 2nd and 3rd colon-separated parts. Strip the `v`
        # prefix from the version, since v<N> is the wire-format of the public key.
        vault_public_key_version = vault_signature_parts[1].removeprefix('v')
        signature = vault_signature_parts[2]

        # Fetch the public key and the attached certificate chain for the exact
        # key version that was used for signing.
        public_key, certificate_chain = self._read_key_version(vault_public_key_version)

        # A certificate chain is REQUIRED for the vault-transit backend to be a complete drop-in for
        # the signing-server.
        if not certificate_chain:
            raise VaultTransitException(
                f'no certificate chain found for transit key {self.cfg.key!r} '
                f'(version {vault_public_key_version})'
            )

        return VaultTransitSigningResponse(
            signature=signature,
            public_key=public_key,
            signing_algorithm=signing_algorithm,
            public_key_version=vault_public_key_version,
            certificate_chain=certificate_chain,
        )

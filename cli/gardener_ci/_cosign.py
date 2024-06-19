__cmd_name__ = 'cosign'

import hashlib

import ccc.oci
import cosign
import signingserver
import oci.model as om


def _payload(
    image_reference: str,
):
    image_reference = om.OciImageReference(image_reference)
    oci_client = ccc.oci.oci_client()

    if not image_reference.has_tag:
        print('Error: image-ref must have tag')
        exit(1)
    if not image_reference.has_digest_tag:
        manifest = oci_client.manifest_raw(
            image_reference=image_reference,
            accept=om.MimeTypes.prefer_multiarch,
        )
        digest = hashlib.sha256(manifest.content).hexdigest()
        image_reference = f'{image_reference.ref_without_tag}@sha256:{digest}'

        print(f'resolved image-ref to {image_reference}')

    return cosign.payload_bytes(
        image_reference=image_reference,
    )


def signature_ref(
    image_reference: str,
):
    signature_image_ref = cosign.default_signature_image_reference(image_reference)
    print(signature_image_ref)


def sign(
    image_reference: str,
    signing_server_url: str,
    signing_server_client_cert: str,
    signing_server_client_cert_key: str,
    on_exist: cosign.OnExist=cosign.OnExist.APPEND,
    signing_server_certificate_ca: str=None,
    signing_algorithm: str='rsassa-pkcs1-v1_5',
    tls_validation: bool=True,
):
    image_reference = om.OciImageReference(image_reference)
    oci_client = ccc.oci.oci_client()

    if not image_reference.has_tag:
        print('Error: image-ref must have tag')
        exit(1)
    if not image_reference.has_digest_tag:
        manifest = oci_client.manifest_raw(
            image_reference=image_reference,
            accept=om.MimeTypes.prefer_multiarch,
        )
        digest = hashlib.sha256(manifest.content).hexdigest()
        image_reference = f'{image_reference.ref_without_tag}@sha256:{digest}'

        print(f'resolved image-ref to {image_reference}')

    payload_bytes = _payload(
        image_reference=image_reference,
    )

    signingserver_client = signingserver.SigningserverClient(
        cfg=signingserver.SigningserverClientCfg(
            base_url=signing_server_url,
            client_certificate=signing_server_client_cert,
            client_certificate_key=signing_server_client_cert_key,
            server_certificate_ca=signing_server_certificate_ca,
            validate_tls_certificate=tls_validation,
        ),
    )

    signature = signingserver_client.sign(
        content=payload_bytes,
        signing_algorithm=signing_algorithm,
    )

    signature_image_ref = cosign.default_signature_image_reference(image_reference)
    print(f'creating signature: {signature_image_ref}')

    cosign.sign_image(
        image_reference=image_reference,
        signature_image_reference=signature_image_ref,
        signature=signature.signature.replace('\n', ''),
        signing_algorithm=signing_algorithm,
        public_key=signature.public_key.replace('\n', ''),
        on_exist=on_exist,
        oci_client=oci_client,
    )

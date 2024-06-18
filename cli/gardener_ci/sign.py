import signingserver


def sign(
    filepath: str,
    signing_server_url: str,
    signing_server_client_cert: str,
    signing_server_client_cert_key: str,
    signing_server_certificate_ca: str=None,
    signing_algorithm: str='rsassa-pss',
    tls_validation: bool=True,
):
    with open(filepath, 'rb') as f:
        payload_bytes = f.read()

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

    print(signature.signature)

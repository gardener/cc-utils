import signingserver


def sign(
    signing_server_url: str,
    signing_server_client_cert: str,
    signing_server_client_cert_key: str,
    digest: str=None,
    filepath: str=None,
    output: str='signature',
    signing_server_certificate_ca: str=None,
    signing_algorithm: str='rsassa-pkcs1-v1_5',
    tls_validation: bool=True,
):
    if filepath:
        with open(filepath, 'rb') as f:
            payload_bytes = f.read()
    else:
        payload_bytes = None

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
        digest=digest,
        signing_algorithm=signing_algorithm,
    )

    if output == 'signature':
        print(signature.signature)
    elif output == 'raw':
        print(signature.raw)
    elif output == 'certificate':
        print(signature.certificate)
    elif output == 'public-key':
        print(signature.public_key)
    else:
        print(f'invalid choice: {output=}')
        exit(1)

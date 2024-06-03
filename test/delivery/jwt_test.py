import datetime
import pytest

import Crypto.PublicKey.RSA
import jwt

import delivery.jwt
import model.delivery

ISSUER = 'test-issuer'


@pytest.fixture
def signing_cfg() -> model.delivery.SigningCfg:
    private_key = Crypto.PublicKey.RSA.generate(4096)

    return model.delivery.SigningCfg({
        'id': '0',
        'algorithm': delivery.jwt.Algorithm.RS256,
        'secret': private_key.export_key(format='PEM'),
        'public_key': private_key.public_key().export_key(format='PEM'),
        'purpose_labels': [],
    })


@pytest.fixture
def token(signing_cfg) -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    time_delta = datetime.timedelta(days=730) # 2 years

    token = {
        'version': 'v1',
        'sub': 'test-user',
        'iss': ISSUER,
        'iat': int(now.timestamp()),
        'github_oAuth': {
            'host': 'github',
            'team_names': [],
            'email_address': 'test-user@email.com',
        },
        'exp': int((now + time_delta).timestamp()),
        'key_id': signing_cfg.id(),
    }

    return jwt.encode(
        payload=token,
        key=signing_cfg.secret(),
        algorithm=signing_cfg.algorithm(),
    )


def test_jwt(signing_cfg, token):
    json_web_key = delivery.jwt.JSONWebKey.from_signing_cfg(
        signing_cfg=signing_cfg,
    )

    # token was just created and thus is not expired yet
    assert not delivery.jwt.is_jwt_token_expired(
        token=token,
        json_web_keys=[
            json_web_key,
        ],
    )

    # this is a correct token validation
    delivery.jwt.decode_jwt(
        token=token,
        verify_signature=True,
        json_web_key=json_web_key,
        issuer=ISSUER,
    )

    # no key supplied but token validation requested
    with pytest.raises(ValueError):
        delivery.jwt.decode_jwt(
            token=token,
            verify_signature=True,
            json_web_key=None,
            issuer=ISSUER,
        )

    # token validation but with wrong issuer
    with pytest.raises(jwt.exceptions.InvalidIssuerError):
        delivery.jwt.decode_jwt(
            token=token,
            verify_signature=True,
            json_web_key=json_web_key,
            issuer='wrong-issuer',
        )

import datetime
import pytest

import Crypto.PublicKey.RSA
import jwt

import delivery.jwt


ISSUER = 'test-issuer'
private_key = Crypto.PublicKey.RSA.generate(4096)
public_key = private_key.public_key()


@pytest.fixture
def json_web_key() -> delivery.jwt.JSONWebKey:
    return delivery.jwt.JSONWebKey.from_dict(
        data={
            'use': delivery.jwt.Use.SIGNATURE,
            'kid': 'foo',
            'alg': delivery.jwt.Algorithm.RS256,
            'n': delivery.jwt.encodeBase64urlUInt(public_key.n),
            'e': delivery.jwt.encodeBase64urlUInt(private_key.e)
        }
    )


@pytest.fixture
def token() -> str:
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
        'key_id': '0',
    }

    return jwt.encode(
        payload=token,
        key=private_key.export_key(format='PEM'),
        algorithm=delivery.jwt.Algorithm.RS256,
    )


def test_jwt(json_web_key, token):
    # token was just created and thus is not expired yet
    assert not delivery.jwt.is_jwt_token_expired(
        token=token,
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

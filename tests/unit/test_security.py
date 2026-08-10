from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from pk_agent.agentos.main import create_app
from pk_agent.config import Settings
from pk_agent.security.jwks import JwksAuthenticator
from pk_agent.security.sse import SseTicketStore
from pk_agent.security.tenant import Principal, parse_bearer_token, principal_from_claims


def test_production_requires_asymmetric_jwt_configuration() -> None:
    settings = Settings(database_url="memory://", environment="production")
    with pytest.raises(ValueError, match="JWT_JWKS_URL"):
        create_app(settings)


def test_production_requires_shared_runtime_store() -> None:
    settings = Settings(
        database_url="memory://",
        environment="production",
        jwt_jwks_url="https://identity.example.com/jwks.json",
        jwt_issuer="https://identity.example.com/",
        jwt_audience="production-knowledge-agent",
        runtime_store="memory",
    )
    with pytest.raises(ValueError, match="RUNTIME_STORE"):
        create_app(settings)


def test_production_rejects_dev_token_and_accepts_expiring_jwt() -> None:
    secret = "a-production-secret-that-is-at-least-32-chars"  # noqa: S105
    with pytest.raises(ValueError, match="disabled"):
        parse_bearer_token("dev:tenant:user:admin", secret, allow_dev_tokens=False)

    token = jwt.encode(
        {
            "sub": "user-1",
            "tenant_id": "tenant-a",
            "roles": ["admin"],
            "exp": int(time.time()) + 60,
        },
        secret,
        algorithm="HS256",
    )
    principal = parse_bearer_token(token, secret, allow_dev_tokens=False)
    assert principal == Principal("tenant-a", "user-1", frozenset({"admin"}))


def _rsa_jwk(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    body = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    body.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return body


def _asymmetric_token(private_key: rsa.RSAPrivateKey, kid: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "user-1",
            "tenant_id": "tenant-a",
            "roles": ["admin"],
            "iss": "https://identity.example.com/",
            "aud": "production-knowledge-agent",
            "iat": now,
            "exp": now + 60,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


async def test_jwks_authenticator_validates_issuer_audience_and_rotates_keys() -> None:
    first_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        keys = [_rsa_jwk(first_key, "key-1")]
        if requests > 1:
            keys.append(_rsa_jwk(rotated_key, "key-2"))
        return httpx.Response(200, json={"keys": keys})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    authenticator = JwksAuthenticator(
        "https://identity.example.com/jwks.json",
        issuer="https://identity.example.com/",
        audience="production-knowledge-agent",
        client=client,
    )

    assert await authenticator.authenticate(_asymmetric_token(first_key, "key-1")) == Principal(
        "tenant-a", "user-1", frozenset({"admin"})
    )
    assert await authenticator.authenticate(_asymmetric_token(rotated_key, "key-2")) == Principal(
        "tenant-a", "user-1", frozenset({"admin"})
    )
    assert requests == 2
    await client.aclose()


async def test_jwks_authenticator_rejects_symmetric_algorithm() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    authenticator = JwksAuthenticator(
        "https://identity.example.com/jwks.json",
        issuer="https://identity.example.com/",
        audience="production-knowledge-agent",
        client=client,
    )
    token = jwt.encode(
        {"sub": "user", "tenant_id": "tenant", "exp": int(time.time()) + 60},
        "a-symmetric-test-secret-at-least-32-bytes",  # noqa: S106
        algorithm="HS256",
        headers={"kid": "shared"},
    )
    with pytest.raises(ValueError, match="algorithm"):
        await authenticator.authenticate(token)
    await client.aclose()


def test_signed_jwt_requires_expiration() -> None:
    secret = "test-secret-that-is-at-least-32-chars"  # noqa: S105
    token = jwt.encode(
        {"sub": "user-1", "tenant_id": "tenant-a", "roles": ["admin"]},
        secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.MissingRequiredClaimError):
        parse_bearer_token(token, secret)


def test_tenant_claim_rejects_checkpoint_delimiter_characters() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        principal_from_claims({"tenant_id": "tenant-a:tenant-b", "sub": "user", "roles": []})


async def test_sse_ticket_is_bound_single_use_and_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr("pk_agent.security.sse.time.monotonic", lambda: now)
    store = SseTicketStore(ttl_seconds=10)
    principal = Principal("tenant-a", "user-1", frozenset({"admin"}))

    wrong_task_ticket = await store.issue(principal, "task-a")
    assert await store.consume(wrong_task_ticket, "task-b") is None

    valid_ticket = await store.issue(principal, "task-a")
    assert await store.consume(valid_ticket, "task-a") is not None
    assert await store.consume(valid_ticket, "task-a") is None

    expired_ticket = await store.issue(principal, "task-a")
    now = 111.0
    assert await store.consume(expired_ticket, "task-a") is None

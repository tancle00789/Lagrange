from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import jwt

from pk_agent.security.tenant import Principal, principal_from_claims

ALLOWED_JWT_ALGORITHMS = frozenset({"RS256", "ES256"})


class JwksAuthenticator:
    """Async JWKS verifier with TTL caching and unknown-key rotation refresh."""

    def __init__(
        self,
        jwks_url: str,
        *,
        issuer: str,
        audience: str,
        cache_seconds: int = 300,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience
        self.cache_seconds = cache_seconds
        self.client = client or httpx.AsyncClient(timeout=5, follow_redirects=False)
        self._owns_client = client is None
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def authenticate(self, token: str) -> Principal:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        algorithm = header.get("alg")
        if not isinstance(kid, str) or not kid:
            raise ValueError("JWT kid header is required")
        if algorithm not in ALLOWED_JWT_ALGORITHMS:
            raise ValueError("JWT algorithm is not allowed")

        key = await self._key(kid)
        if key.algorithm_name and key.algorithm_name != algorithm:
            raise ValueError("JWT algorithm does not match JWKS key")
        claims = jwt.decode(
            token,
            key.key,
            algorithms=[algorithm],
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "sub", "tenant_id", "iss", "aud"]},
        )
        return principal_from_claims(claims)

    async def _key(self, kid: str) -> Any:
        if time.monotonic() >= self._expires_at:
            await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            await self._refresh(force=True)
            key = self._keys.get(kid)
        if key is None:
            raise ValueError("JWT signing key was not found")
        return key

    async def _refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if not force and self._keys and time.monotonic() < self._expires_at:
                return
            response = await self.client.get(self.jwks_url)
            response.raise_for_status()
            body: Any = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
                raise ValueError("JWKS response has invalid shape")
            parsed = jwt.PyJWKSet.from_dict(body)  # type: ignore[attr-defined]
            keys = {
                key.key_id: key
                for key in parsed.keys
                if key.key_id and key.algorithm_name in ALLOWED_JWT_ALGORITHMS
            }
            if not keys:
                raise ValueError("JWKS contains no allowed signing keys")
            self._keys = keys
            self._expires_at = time.monotonic() + self.cache_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

from __future__ import annotations

import hashlib
import time
from typing import Protocol

from fastapi import Depends, Header, HTTPException, Request
from redis.asyncio import Redis

from pk_agent.config import Settings, get_settings
from pk_agent.observability import bind_tenant
from pk_agent.security.tenant import Principal, parse_bearer_token


class RateLimit(Protocol):
    async def check(self, key: str) -> None: ...


class RateLimiter:
    def __init__(self, rps: int) -> None:
        self.rps = rps
        self.hits: dict[str, list[float]] = {}

    async def check(self, key: str) -> None:
        now = time.monotonic()
        hits = [item for item in self.hits.get(key, []) if now - item < 1]
        if len(hits) >= self.rps:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        hits.append(now)
        self.hits[key] = hits


class RedisRateLimiter:
    def __init__(
        self,
        client: Redis,
        rps: int,
        *,
        key_prefix: str = "pk_agent",
    ) -> None:
        self.client = client
        self.rps = rps
        self.key_prefix = key_prefix

    async def check(self, key: str) -> None:
        digest = hashlib.sha256(key.encode()).hexdigest()
        window = int(time.time())
        redis_key = f"{self.key_prefix}:rate:{digest}:{window}"
        async with self.client.pipeline(transaction=True) as pipeline:
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, 2)
            count, _ = await pipeline.execute()
        if int(count) > self.rps:
            raise HTTPException(status_code=429, detail="rate limit exceeded")


async def get_current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Principal:
    raw: str | None = None
    if authorization and authorization.startswith("Bearer "):
        raw = authorization.removeprefix("Bearer ")
    if not raw:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        authenticator = getattr(request.app.state, "jwks_authenticator", None)
        if authenticator is not None:
            principal = await authenticator.authenticate(raw)
        else:
            principal = parse_bearer_token(
                raw,
                settings.jwt_secret,
                allow_dev_tokens=settings.environment.lower() != "production",
            )
        bind_tenant(principal.tenant_id)
        return principal
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid bearer token") from exc


async def enforce_rate_limit(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    await request.app.state.rate_limiter.check(principal.tenant_id)
    return principal

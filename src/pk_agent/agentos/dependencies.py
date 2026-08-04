from __future__ import annotations

import time

from fastapi import Depends, Header, HTTPException, Query

from pk_agent.config import Settings, get_settings
from pk_agent.security.tenant import Principal, parse_bearer_token


class RateLimiter:
    def __init__(self, rps: int) -> None:
        self.rps = rps
        self.hits: dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = [item for item in self.hits.get(key, []) if now - item < 1]
        if len(hits) >= self.rps:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        hits.append(now)
        self.hits[key] = hits


_limiter: RateLimiter | None = None


def get_limiter(settings: Settings) -> RateLimiter:
    global _limiter
    if _limiter is None or _limiter.rps != settings.rate_limit_rps:
        _limiter = RateLimiter(settings.rate_limit_rps)
    return _limiter


async def get_current_principal(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> Principal:
    raw: str | None = None
    if authorization and authorization.startswith("Bearer "):
        raw = authorization.removeprefix("Bearer ")
    elif token:
        # EventSource cannot set headers; SSE clients pass ?token= instead.
        raw = token
    if not raw:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return parse_bearer_token(raw, settings.jwt_secret)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid bearer token") from exc


async def enforce_rate_limit(
    principal: Principal = Depends(get_current_principal),
    settings: Settings = Depends(get_settings),
) -> Principal:
    get_limiter(settings).check(principal.tenant_id)
    return principal

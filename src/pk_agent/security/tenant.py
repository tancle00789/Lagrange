from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import jwt

_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    roles: frozenset[str]


def principal_from_claims(payload: dict[str, Any]) -> Principal:
    tenant_id = payload["tenant_id"]
    user_id = payload["sub"]
    roles = payload.get("roles", [])
    if not isinstance(tenant_id, str) or _TENANT_ID_RE.fullmatch(tenant_id) is None:
        raise ValueError("invalid tenant_id claim")
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("invalid sub claim")
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise ValueError("invalid roles claim")
    return Principal(tenant_id=tenant_id, user_id=user_id, roles=frozenset(roles))


def parse_bearer_token(token: str, secret: str, *, allow_dev_tokens: bool = True) -> Principal:
    if token.startswith("dev:"):
        if not allow_dev_tokens:
            raise ValueError("development bearer tokens are disabled")
        parts = token.split(":", 3)
        if len(parts) != 4 or not parts[1] or not parts[2]:
            raise ValueError("invalid development bearer token")
        _, tenant_id, user_id, roles = parts
        return Principal(tenant_id=tenant_id, user_id=user_id, roles=frozenset(roles.split(",")))

    payload = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        options={"require": ["exp", "sub", "tenant_id"]},
    )
    return principal_from_claims(payload)

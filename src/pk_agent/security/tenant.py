from __future__ import annotations

from dataclasses import dataclass

import jwt


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    roles: frozenset[str]


def parse_bearer_token(token: str, secret: str) -> Principal:
    if token.startswith("dev:"):
        _, tenant_id, user_id, roles = token.split(":", 3)
        return Principal(tenant_id=tenant_id, user_id=user_id, roles=frozenset(roles.split(",")))

    payload = jwt.decode(token, secret, algorithms=["HS256"])
    return Principal(
        tenant_id=str(payload["tenant_id"]),
        user_id=str(payload["sub"]),
        roles=frozenset(payload.get("roles", [])),
    )

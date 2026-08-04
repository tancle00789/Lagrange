from __future__ import annotations

from pk_agent.security.tenant import Principal


def require_role(principal: Principal, role: str) -> None:
    if role not in principal.roles:
        raise PermissionError(f"missing role: {role}")

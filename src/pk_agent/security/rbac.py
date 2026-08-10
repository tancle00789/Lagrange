from __future__ import annotations

from fastapi import HTTPException

from pk_agent.security.tenant import Principal


def require_role(principal: Principal, role: str) -> None:
    if role not in principal.roles:
        raise HTTPException(status_code=403, detail=f"missing role: {role}")

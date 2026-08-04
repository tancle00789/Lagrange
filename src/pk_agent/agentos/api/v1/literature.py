from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from pk_agent.agentos.dependencies import enforce_rate_limit
from pk_agent.harness.registry import ToolRisk
from pk_agent.security.tenant import Principal

router = APIRouter()


class LiteratureSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    sources: list[str] | None = Field(default=None, max_length=8)
    limit: int = Field(default=5, ge=1, le=20)


class LiteratureSearchResponse(BaseModel):
    records: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


@router.post("/search", response_model=LiteratureSearchResponse)
async def search_literature(
    payload: LiteratureSearchRequest,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> LiteratureSearchResponse:
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="literature subsystem unavailable")
    body: dict = {"query": payload.query, "limit": payload.limit}
    if payload.sources:
        body["sources"] = payload.sources
    result = await registry.call("literature_search", body, max_risk=ToolRisk.L1)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error_message)
    data = result.data or {}
    return LiteratureSearchResponse(
        records=data.get("records") or [],
        errors=data.get("errors") or [],
    )

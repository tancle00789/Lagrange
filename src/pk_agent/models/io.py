from __future__ import annotations

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    require_approval: bool = False
    allow_external: bool = False
    ingest_external: bool = False


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str


class SseTicketResponse(BaseModel):
    ticket: str
    expires_in: int


class TaskStatusResponse(BaseModel):
    task_id: str
    tenant_id: str
    status: str
    result: str | None = None
    report: dict | None = None
    knowledge_hits: list[dict] = Field(default_factory=list)
    external_sources: list[dict] = Field(default_factory=list)
    messages: list[dict] = Field(default_factory=list)


class ResumeRequest(BaseModel):
    approved: bool = True
    note: str | None = Field(default=None, max_length=1000)

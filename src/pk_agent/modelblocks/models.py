from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

BlockKind = Literal["sklearn", "torch", "sim"]
RunStatus = Literal["completed", "failed"]


class ModelBlockSpec(BaseModel):
    name: str
    version: str
    description: str
    kind: BlockKind
    gpu_required: bool = False
    max_runtime_seconds: int = 900


class ModelRunRequest(BaseModel):
    run_id: str
    tenant_id: str
    task_id: str
    block_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ModelRunResult(BaseModel):
    run_id: str
    block_name: str
    status: RunStatus
    metrics: dict[str, float] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0

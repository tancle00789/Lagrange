from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from pk_agent.modelblocks.artifacts import ArtifactStore
from pk_agent.modelblocks.models import ModelBlockSpec, ModelRunResult


@dataclass
class RunContext:
    tenant_id: str
    task_id: str
    run_id: str
    artifacts: ArtifactStore
    logger: logging.Logger

    def artifact_key(self, filename: str) -> str:
        return f"{self.tenant_id}/{self.task_id}/{self.run_id}-{filename}"


@runtime_checkable
class ModelBlock(Protocol):
    @property
    def spec(self) -> ModelBlockSpec: ...

    @property
    def params_model(self) -> type[BaseModel]: ...

    async def run(self, params: BaseModel, ctx: RunContext) -> ModelRunResult: ...

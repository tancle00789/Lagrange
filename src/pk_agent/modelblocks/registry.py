from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from pk_agent.modelblocks.base import ModelBlock
from pk_agent.modelblocks.models import ModelBlockSpec


class ModelBlockRegistry:
    """Explicitly-registered catalog of trusted model blocks.

    Blocks are pre-registered at startup; there is no implicit registration
    path and no way for callers to inject new code.
    """

    def __init__(self) -> None:
        self._blocks: dict[str, ModelBlock] = {}

    def register(self, block: ModelBlock) -> None:
        name = block.spec.name
        if name in self._blocks:
            raise ValueError(f"model block {name!r} already registered")
        self._blocks[name] = block

    def get(self, name: str) -> ModelBlock | None:
        return self._blocks.get(name)

    def catalog(self) -> list[ModelBlockSpec]:
        return [block.spec for block in self._blocks.values()]

    def catalog_with_schemas(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for block in self._blocks.values():
            entries.append(
                {
                    **block.spec.model_dump(),
                    "params_schema": block.params_model.model_json_schema(),
                }
            )
        return entries

    def validate_params(
        self, name: str, params: dict[str, Any]
    ) -> tuple[BaseModel | None, str | None]:
        """Return (validated_params, error). error is None on success."""
        block = self.get(name)
        if block is None:
            return None, f"unknown model block: {name!r}"
        try:
            return block.params_model.model_validate(params), None
        except ValidationError as exc:
            return None, f"params failed schema for {name!r}: {exc.error_count()} error(s)"

    def catalog_summary_json(self) -> str:
        """Compact catalog description for the plan prompt."""
        summary = []
        for block in self._blocks.values():
            schema = block.params_model.model_json_schema()
            props = {
                k: v.get("type", "any") for k, v in schema.get("properties", {}).items()
            }
            summary.append(
                {
                    "name": block.spec.name,
                    "description": block.spec.description,
                    "kind": block.spec.kind,
                    "gpu_required": block.spec.gpu_required,
                    "params": props,
                    "required": schema.get("required", []),
                }
            )
        return json.dumps(summary, ensure_ascii=False)

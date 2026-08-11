from __future__ import annotations

from typing import Any
from uuid import uuid4

from pk_agent.harness.registry import ToolHandler, ToolRisk, ToolSpec, TransientToolError
from pk_agent.modelblocks.broker import ModelRunBroker
from pk_agent.modelblocks.models import ModelRunRequest
from pk_agent.modelblocks.registry import ModelBlockRegistry

MODEL_BLOCK_RUN_TOOL = "model_block_run"

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "block_name": {"type": "string"},
        "params": {"type": "object"},
        "tenant_id": {"type": "string"},
        "task_id": {"type": "string"},
    },
    "required": ["block_name", "params", "tenant_id", "task_id"],
    "additionalProperties": False,
}


def make_model_run_handler(
    registry: ModelBlockRegistry,
    broker: ModelRunBroker,
    *,
    timeout_seconds: float,
) -> ToolHandler:
    """Build the ToolRegistry handler for `model_block_run`.

    The payload's params are re-validated against the block's Pydantic schema
    here — the graph's plan is not trusted blindly.
    """

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        block_name = payload.get("block_name")
        params = payload.get("params")
        tenant_id = payload.get("tenant_id")
        task_id = payload.get("task_id")
        if not isinstance(block_name, str) or not block_name:
            raise ValueError("payload.block_name must be a non-empty string")
        if not isinstance(params, dict):
            raise ValueError("payload.params must be an object")
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("payload.tenant_id must be a non-empty string")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("payload.task_id must be a non-empty string")

        # Server-side re-validation of the block schema.
        _, error = registry.validate_params(block_name, params)
        if error is not None:
            raise ValueError(error)

        request = ModelRunRequest(
            run_id=uuid4().hex,
            tenant_id=tenant_id,
            task_id=task_id,
            block_name=block_name,
            params=params,
        )
        try:
            await broker.submit(request)
        except Exception as exc:  # redis connectivity — retryable
            raise TransientToolError(f"model run submit failed: {exc}") from exc

        result = await broker.await_result(
            request.run_id, timeout_seconds=timeout_seconds
        )
        if result is None:
            raise TransientToolError(
                f"model run {request.run_id} timed out after {timeout_seconds}s"
            )
        return result.model_dump()

    return handler


def model_block_run_spec(
    registry: ModelBlockRegistry,
    broker: ModelRunBroker,
    *,
    timeout_seconds: float,
) -> ToolSpec:
    return ToolSpec(
        name=MODEL_BLOCK_RUN_TOOL,
        version="1.0.0",
        description=(
            "Run a pre-registered model block (sklearn/torch/simulation) in the "
            "executor service and return its metrics and artifact ids."
        ),
        risk=ToolRisk.L2,
        handler=make_model_run_handler(registry, broker, timeout_seconds=timeout_seconds),
        timeout_seconds=timeout_seconds + 30.0,
        max_retries=0,  # a timed-out run may have side effects; never auto-retry
        requires_approval=False,
        idempotency_strategy="none",
        input_schema=_INPUT_SCHEMA,
        output_schema=None,
    )

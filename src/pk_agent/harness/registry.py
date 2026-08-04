from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal


class ToolRisk(IntEnum):
    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4


ToolHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]
IdempotencyStrategy = Literal["none", "key", "natural"]
ToolStatus = Literal["ok", "error"]


class TransientToolError(Exception):
    """Retryable failure (connection errors, 5xx, throttling)."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    risk: ToolRisk
    handler: ToolHandler
    timeout_seconds: float = 30.0
    max_retries: int = 2
    requires_approval: bool = False
    idempotency_strategy: IdempotencyStrategy = "none"
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    status: ToolStatus
    data: Any = None
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int = 0
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class ToolRegistry:
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        if not spec.name:
            raise ValueError("tool spec requires a name")
        self._tools[spec.name] = spec

    def get(self, name: str, *, max_risk: ToolRisk = ToolRisk.L1) -> ToolSpec:
        spec = self._tools[name]
        if spec.risk > max_risk:
            raise PermissionError(f"tool {name} risk {spec.risk} exceeds {max_risk}")
        return spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def call(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        max_risk: ToolRisk = ToolRisk.L1,
        approved: bool = False,
    ) -> ToolResult:
        call_id = uuid.uuid4().hex
        started = time.monotonic()

        def _error(code: str, message: str, *, retryable: bool = False) -> ToolResult:
            return ToolResult(
                call_id=call_id,
                status="error",
                error_code=code,
                error_message=message,
                duration_ms=int((time.monotonic() - started) * 1000),
                retryable=retryable,
            )

        spec = self._tools.get(name)
        if spec is None:
            return _error("not_found", f"tool {name} is not registered")
        if spec.risk > max_risk:
            return _error(
                "risk_denied",
                f"tool {name} risk {spec.risk} exceeds {max_risk}",
            )
        if spec.requires_approval and not approved:
            return _error(
                "approval_required",
                f"tool {name} requires approval before execution",
            )

        attempts = 1 + max(0, spec.max_retries)
        last_error: ToolResult | None = None
        for attempt in range(attempts):
            try:
                outcome = spec.handler(payload)
                if inspect.isawaitable(outcome):
                    outcome = await asyncio.wait_for(
                        outcome, timeout=spec.timeout_seconds
                    )
                return ToolResult(
                    call_id=call_id,
                    status="ok",
                    data=outcome,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            except (TimeoutError, TransientToolError) as exc:
                last_error = _error(
                    "timeout" if isinstance(exc, TimeoutError) else "transient",
                    str(exc) or type(exc).__name__,
                    retryable=True,
                )
                if attempt < attempts - 1:
                    await asyncio.sleep(min(2.0**attempt, 8.0))
            except Exception as exc:  # noqa: BLE001 - wrapped into ToolResult
                return _error("handler_error", f"{type(exc).__name__}: {exc}")
        assert last_error is not None
        return last_error


@dataclass
class SkillRegistry:
    _skills: dict[str, str] = field(default_factory=dict)

    def register(self, name: str, instructions: str) -> None:
        self._skills[name] = instructions

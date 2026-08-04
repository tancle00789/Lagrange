from __future__ import annotations

import asyncio
from typing import Any

from pk_agent.harness.registry import (
    ToolRegistry,
    ToolRisk,
    ToolSpec,
    TransientToolError,
)


def _spec(
    name: str = "tool",
    *,
    risk: ToolRisk = ToolRisk.L0,
    handler: Any = None,
    timeout_seconds: float = 5.0,
    max_retries: int = 0,
    requires_approval: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1.0",
        description="test tool",
        risk=risk,
        handler=handler or (lambda payload: {"echo": payload}),
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        requires_approval=requires_approval,
    )


async def test_ok_result_wraps_data() -> None:
    registry = ToolRegistry()
    registry.register(_spec())
    result = await registry.call("tool", {"x": 1})
    assert result.ok
    assert result.data == {"echo": {"x": 1}}
    assert result.call_id
    assert result.duration_ms >= 0


async def test_async_handler_supported() -> None:
    async def handler(payload: dict[str, Any]) -> str:
        return "async-ok"

    registry = ToolRegistry()
    registry.register(_spec(handler=handler))
    result = await registry.call("tool", {})
    assert result.ok and result.data == "async-ok"


async def test_unknown_tool_returns_not_found() -> None:
    registry = ToolRegistry()
    result = await registry.call("missing", {})
    assert not result.ok
    assert result.error_code == "not_found"


async def test_risk_gate_denies_high_risk_tool() -> None:
    registry = ToolRegistry()
    registry.register(_spec(risk=ToolRisk.L3))
    result = await registry.call("tool", {}, max_risk=ToolRisk.L1)
    assert not result.ok
    assert result.error_code == "risk_denied"

    allowed = await registry.call("tool", {}, max_risk=ToolRisk.L3)
    assert allowed.ok


async def test_requires_approval_gate() -> None:
    registry = ToolRegistry()
    registry.register(_spec(requires_approval=True))
    denied = await registry.call("tool", {})
    assert not denied.ok
    assert denied.error_code == "approval_required"

    approved = await registry.call("tool", {}, approved=True)
    assert approved.ok


async def test_timeout_is_retryable_and_bounded() -> None:
    calls = {"count": 0}

    async def slow(payload: dict[str, Any]) -> None:
        calls["count"] += 1
        await asyncio.sleep(1)

    registry = ToolRegistry()
    registry.register(_spec(handler=slow, timeout_seconds=0.05, max_retries=1))
    result = await registry.call("tool", {})
    assert not result.ok
    assert result.error_code == "timeout"
    assert result.retryable
    assert calls["count"] == 2  # initial attempt + one retry


async def test_transient_error_retried_then_succeeds() -> None:
    calls = {"count": 0}

    async def flaky(payload: dict[str, Any]) -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise TransientToolError("try again")
        return "recovered"

    registry = ToolRegistry()
    registry.register(_spec(handler=flaky, max_retries=3))
    result = await registry.call("tool", {})
    assert result.ok and result.data == "recovered"
    assert calls["count"] == 3


async def test_handler_error_not_retried() -> None:
    calls = {"count": 0}

    def broken(payload: dict[str, Any]) -> None:
        calls["count"] += 1
        raise ValueError("bad payload")

    registry = ToolRegistry()
    registry.register(_spec(handler=broken, max_retries=3))
    result = await registry.call("tool", {})
    assert not result.ok
    assert result.error_code == "handler_error"
    assert not result.retryable
    assert calls["count"] == 1


def test_register_rejects_empty_name() -> None:
    registry = ToolRegistry()
    try:
        registry.register(_spec(name=""))
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty tool name")

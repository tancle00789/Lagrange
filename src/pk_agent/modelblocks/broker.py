from __future__ import annotations

import asyncio
import logging
import time

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from pk_agent.modelblocks.artifacts import ArtifactStore
from pk_agent.modelblocks.base import RunContext
from pk_agent.modelblocks.models import ModelRunRequest, ModelRunResult
from pk_agent.modelblocks.registry import ModelBlockRegistry

logger = logging.getLogger(__name__)


class ModelRunBroker:
    """API-side broker: submits model-run requests to a Redis stream and
    awaits results published by the executor service."""

    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "pk_agent",
        result_ttl_seconds: int = 3600,
        maxlen: int = 10_000,
    ) -> None:
        self.client = client
        self.stream = f"{key_prefix}:modelruns"
        self.done_channel = f"{key_prefix}:modelruns:done"
        self.result_ttl_seconds = result_ttl_seconds
        self.maxlen = maxlen

    def _result_key(self, run_id: str) -> str:
        return f"{self.stream}:result:{run_id}"

    async def submit(self, request: ModelRunRequest) -> str:
        await self.client.xadd(
            self.stream,
            {"payload": request.model_dump_json()},
            maxlen=self.maxlen,
            approximate=True,
        )
        return request.run_id

    async def await_result(
        self, run_id: str, *, timeout_seconds: float
    ) -> ModelRunResult | None:
        """Wait for the executor to publish the result. Returns None on timeout."""
        deadline = time.monotonic() + timeout_seconds

        # Result may already be there (fast executor, or retry).
        result = await self._read_result(run_id)
        if result is not None:
            return result

        pubsub = self.client.pubsub()
        try:
            await pubsub.subscribe(self.done_channel)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                wait = min(1.0, remaining)  # 1s poll fallback alongside pubsub
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=wait
                        ),
                        timeout=wait + 0.5,
                    )
                except TimeoutError:
                    message = None
                if message is not None and message.get("data") != run_id:
                    # completion of another run; fall through to result check anyway
                    pass
                result = await self._read_result(run_id)
                if result is not None:
                    return result
        finally:
            await pubsub.unsubscribe(self.done_channel)
            # types-redis stubs lag redis-py: aclose() exists at runtime (5.x).
            await pubsub.aclose()  # type: ignore[attr-defined]

    async def _read_result(self, run_id: str) -> ModelRunResult | None:
        raw = await self.client.get(self._result_key(run_id))
        if raw is None:
            return None
        try:
            return ModelRunResult.model_validate_json(raw)
        except ValidationError:
            logger.error("corrupt model-run result for %s", run_id)
            return None

    async def _publish_result(self, result: ModelRunResult) -> None:
        await self.client.set(
            self._result_key(result.run_id),
            result.model_dump_json(),
            ex=self.result_ttl_seconds,
        )
        await self.client.publish(self.done_channel, result.run_id)


async def run_model_worker(
    registry: ModelBlockRegistry,
    artifacts: ArtifactStore,
    client: Redis,
    *,
    key_prefix: str = "pk_agent",
    group: str = "executors",
    consumer: str = "executor-0",
    block_ms: int = 1_000,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Executor-side consume loop: read run requests, execute the block,
    upload artifacts, publish the result."""
    stream = f"{key_prefix}:modelruns"
    group_name = f"{key_prefix}:{group}"
    broker = ModelRunBroker(client, key_prefix=key_prefix)

    try:
        await client.xgroup_create(stream, group_name, id="0-0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    while stop_event is None or not stop_event.is_set():
        try:
            messages = await client.xreadgroup(
                group_name,
                consumer,
                {stream: ">"},
                count=1,
                block=block_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("model-run consumer read failed; retrying")
            await asyncio.sleep(block_ms / 1_000)
            continue
        if not messages:
            # Real Redis blocks for block_ms; some fakes return immediately —
            # sleep so the loop never busy-spins either way.
            await asyncio.sleep(block_ms / 1_000)
            continue
        _, entries = messages[0]
        for message_id, fields in entries:
            try:
                await _process_one(
                    registry, artifacts, broker, dict(fields)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("model-run processing failed for %s", message_id)
            await client.xack(stream, group_name, message_id)


async def _process_one(
    registry: ModelBlockRegistry,
    artifacts: ArtifactStore,
    broker: ModelRunBroker,
    fields: dict[str, str],
) -> None:
    started = time.monotonic()
    try:
        request = ModelRunRequest.model_validate_json(fields["payload"])
    except (KeyError, ValidationError) as exc:
        logger.error("invalid model-run payload dropped: %s", exc)
        return

    logger.info(
        "model run %s: block=%s tenant=%s task=%s",
        request.run_id,
        request.block_name,
        request.tenant_id,
        request.task_id,
    )
    ctx = RunContext(
        tenant_id=request.tenant_id,
        task_id=request.task_id,
        run_id=request.run_id,
        artifacts=artifacts,
        logger=logger,
    )

    params, error = registry.validate_params(request.block_name, request.params)
    if error is not None or params is None:
        result = ModelRunResult(
            run_id=request.run_id,
            block_name=request.block_name,
            status="failed",
            error=error or "unknown validation failure",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    else:
        block = registry.get(request.block_name)
        assert block is not None  # validated above
        try:
            result = await asyncio.wait_for(
                block.run(params, ctx),
                timeout=block.spec.max_runtime_seconds,
            )
        except TimeoutError:
            result = ModelRunResult(
                run_id=request.run_id,
                block_name=request.block_name,
                status="failed",
                error=f"exceeded max_runtime_seconds={block.spec.max_runtime_seconds}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    await broker._publish_result(result)
    logger.info(
        "model run %s finished: status=%s duration_ms=%d",
        result.run_id,
        result.status,
        result.duration_ms,
    )

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError


class RetryableJobError(Exception):
    """The durable delivery should be attempted again by this worker."""


@dataclass(frozen=True)
class JobEnvelope:
    task_id: str
    initial_state: dict[str, Any]
    config: dict[str, Any]
    trace_context: dict[str, str] = field(default_factory=dict)

    def fields(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "initial_state": json.dumps(self.initial_state, separators=(",", ":")),
            "config": json.dumps(self.config, separators=(",", ":")),
            "trace_context": json.dumps(self.trace_context, separators=(",", ":")),
        }

    @classmethod
    def from_fields(cls, fields: dict[str, str]) -> JobEnvelope:
        return cls(
            task_id=fields["task_id"],
            initial_state=dict(json.loads(fields["initial_state"])),
            config=dict(json.loads(fields["config"])),
            trace_context=dict(json.loads(fields.get("trace_context", "{}"))),
        )


JobHandler = Callable[[JobEnvelope], Awaitable[None]]
logger = logging.getLogger(__name__)


class RedisJobQueue:
    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "pk_agent",
        group: str = "workers",
        concurrency: int = 2,
        lease_ms: int = 30_000,
        block_ms: int = 1_000,
        retry_backoff_seconds: float = 0.25,
        maxlen: int = 10_000,
        consumer_prefix: str | None = None,
    ) -> None:
        if concurrency < 1 or lease_ms < 1 or block_ms < 1:
            raise ValueError("worker concurrency, lease and block values must be positive")
        self.client = client
        self.stream = f"{key_prefix}:jobs"
        self.group = f"{key_prefix}:{group}"
        self.concurrency = concurrency
        self.lease_ms = lease_ms
        self.block_ms = block_ms
        self.retry_backoff_seconds = retry_backoff_seconds
        self.maxlen = maxlen
        prefix = consumer_prefix or f"{socket.gethostname()}:{os.getpid()}"
        self.consumer_prefix = prefix
        self._workers: list[asyncio.Task[None]] = []

    async def setup(self) -> None:
        try:
            await self.client.xgroup_create(
                self.stream,
                self.group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def submit(self, job: JobEnvelope) -> str:
        message_id = await self.client.xadd(
            self.stream,
            job.fields(),
            maxlen=self.maxlen,
            approximate=True,
        )
        return str(message_id)

    async def start(self, handler: JobHandler) -> None:
        if self._workers:
            return
        await self.setup()
        for index in range(self.concurrency):
            consumer = f"{self.consumer_prefix}:{index}:{uuid4().hex[:8]}"
            self._workers.append(
                asyncio.create_task(
                    self._consume(consumer, handler),
                    name=f"pk-agent-worker-{index}",
                )
            )

    async def _consume(self, consumer: str, handler: JobHandler) -> None:
        while True:
            try:
                messages = await self.client.xreadgroup(
                    self.group,
                    consumer,
                    {self.stream: ">"},
                    count=1,
                )
                if messages:
                    _, entries = messages[0]
                    for message_id, fields in entries:
                        await self._process(consumer, str(message_id), dict(fields), handler)
                    continue
                claimed = await self._claim_one(consumer)
                if claimed is not None:
                    await self._process(consumer, claimed[0], claimed[1], handler)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("job consumer failed; pending delivery remains recoverable")
            await asyncio.sleep(self.block_ms / 1_000)

    async def _claim_one(self, consumer: str) -> tuple[str, dict[str, str]] | None:
        response = await self.client.xautoclaim(
            self.stream,
            self.group,
            consumer,
            self.lease_ms,
            "0-0",
            count=1,
        )
        entries = response[1]
        if not entries:
            return None
        message_id, fields = entries[0]
        return str(message_id), dict(fields)

    async def _process(
        self,
        consumer: str,
        message_id: str,
        fields: dict[str, str],
        handler: JobHandler,
    ) -> None:
        job = JobEnvelope.from_fields(fields)
        while True:
            delivery: asyncio.Future[None] = asyncio.ensure_future(handler(job))
            heartbeat = asyncio.create_task(
                self._heartbeat(consumer, message_id, delivery),
                name=f"pk-agent-heartbeat-{job.task_id}",
            )
            try:
                await delivery
            except RetryableJobError:
                await asyncio.sleep(self.retry_backoff_seconds)
                continue
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if delivery.cancelled() and current is not None and current.cancelling() == 0:
                    await self.client.xack(self.stream, self.group, message_id)
                    return
                raise
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            await self.client.xack(self.stream, self.group, message_id)
            return

    async def _heartbeat(
        self,
        consumer: str,
        message_id: str,
        delivery: asyncio.Future[None],
    ) -> None:
        interval = max(self.lease_ms / 3 / 1_000, 0.01)
        while not delivery.done():
            await asyncio.sleep(interval)
            await self.client.xclaim(
                self.stream,
                self.group,
                consumer,
                0,
                [message_id],
                justid=True,
            )

    async def aclose(self) -> None:
        workers = list(self._workers)
        self._workers.clear()
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

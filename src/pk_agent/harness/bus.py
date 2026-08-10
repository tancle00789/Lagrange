from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any, Protocol

from redis.asyncio import Redis


class EventBus(Protocol):
    async def publish(self, task_id: str, event: dict[str, Any]) -> None: ...

    def subscribe(self, task_id: str) -> AsyncIterator[dict[str, Any]]: ...


class AgentEventBus:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    async def publish(self, task_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._queues.get(task_id, set())):
            await queue.put(event)

    async def subscribe(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._queues[task_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues[task_id].discard(queue)


class RedisAgentEventBus:
    def __init__(self, client: Redis, *, key_prefix: str = "pk_agent") -> None:
        self.client = client
        self.key_prefix = key_prefix

    def _channel(self, task_id: str) -> str:
        return f"{self.key_prefix}:events:{task_id}"

    async def publish(self, task_id: str, event: dict[str, Any]) -> None:
        await self.client.publish(
            self._channel(task_id),
            json.dumps(event, separators=(",", ":")),
        )

    async def subscribe(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        pubsub = self.client.pubsub()
        await pubsub.subscribe(self._channel(task_id))
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = json.loads(message["data"])
                if isinstance(data, dict):
                    yield data
        finally:
            await pubsub.unsubscribe(self._channel(task_id))
            close = getattr(pubsub, "aclose", None)
            if close is not None:
                await close()
            else:  # pragma: no cover - redis-py <5 type compatibility
                await pubsub.close()

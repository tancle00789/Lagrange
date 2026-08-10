from __future__ import annotations

import asyncio
from collections.abc import Callable

from redis.asyncio import Redis


class CancellationCoordinator:
    async def request(self, task_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


class RedisCancellationCoordinator(CancellationCoordinator):
    def __init__(self, client: Redis, *, key_prefix: str = "pk_agent") -> None:
        self.client = client
        self.channel = f"{key_prefix}:control:cancel"
        self._listener: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def start(self, cancel_owned: Callable[[str], None]) -> None:
        if self._listener is not None:
            return

        async def listen() -> None:
            pubsub = self.client.pubsub()
            await pubsub.subscribe(self.channel)
            self._ready.set()
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        cancel_owned(str(message["data"]))
            finally:
                await pubsub.unsubscribe(self.channel)
                close = getattr(pubsub, "aclose", None)
                if close is not None:
                    await close()
                else:  # pragma: no cover - redis-py <5 type compatibility
                    await pubsub.close()

        self._listener = asyncio.create_task(listen(), name="pk-agent-cancel-listener")
        ready_wait = asyncio.create_task(self._ready.wait())
        done, _ = await asyncio.wait(
            {self._listener, ready_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._listener in done:
            ready_wait.cancel()
            await asyncio.gather(ready_wait, return_exceptions=True)
            await self._listener
        await ready_wait

    async def request(self, task_id: str) -> None:
        await self.client.publish(self.channel, task_id)

    async def aclose(self) -> None:
        if self._listener is None:
            return
        self._listener.cancel()
        await asyncio.gather(self._listener, return_exceptions=True)
        self._listener = None
        self._ready.clear()

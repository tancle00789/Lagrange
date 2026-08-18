from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis


class SkillRefreshCoordinator:
    """No-op coordinator for single-process runtimes (dev/tests)."""

    async def request(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class RedisSkillRefreshCoordinator(SkillRefreshCoordinator):
    """Cross-replica skill-registry invalidation via Redis pub/sub.

    Approving or deprecating a skill changes what the runtime registry may
    inject into prompts; every replica must reload. Mirrors the cancellation
    coordinator's listener pattern (ADR 009).
    """

    def __init__(self, client: Redis, *, key_prefix: str = "pk_agent") -> None:
        self.client = client
        self.channel = f"{key_prefix}:control:skills"
        self._listener: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def start(self, refresh: Callable[[], Awaitable[None]]) -> None:
        if self._listener is not None:
            return

        async def listen() -> None:
            pubsub = self.client.pubsub()
            await pubsub.subscribe(self.channel)
            self._ready.set()
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        await refresh()
            finally:
                await pubsub.unsubscribe(self.channel)
                close = getattr(pubsub, "aclose", None)
                if close is not None:
                    await close()
                else:  # pragma: no cover - redis-py <5 type compatibility
                    await pubsub.close()

        self._listener = asyncio.create_task(listen(), name="pk-agent-skill-listener")
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

    async def request(self) -> None:
        await self.client.publish(self.channel, "refresh")

    async def aclose(self) -> None:
        if self._listener is None:
            return
        self._listener.cancel()
        await asyncio.gather(self._listener, return_exceptions=True)
        self._listener = None
        self._ready.clear()

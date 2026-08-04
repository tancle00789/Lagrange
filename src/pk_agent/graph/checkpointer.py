from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver


async def open_checkpointer(database_url: str) -> tuple[Any, Callable[[], Awaitable[None]]]:
    if database_url.startswith("postgres"):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        context = AsyncPostgresSaver.from_conn_string(database_url)
        saver = await context.__aenter__()
        setup = getattr(saver, "setup", None)
        if setup is not None:
            await setup()

        async def close() -> None:
            await context.__aexit__(None, None, None)

        return saver, close

    async def noop() -> None:
        return None

    return InMemorySaver(), noop

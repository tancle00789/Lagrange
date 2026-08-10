from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.memory import InMemorySaver

_RLS_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _tenant_config(config: RunnableConfig) -> tuple[str, str]:
    configurable = config.get("configurable") or {}
    tenant_id = configurable.get("tenant_id")
    thread_id = configurable.get("thread_id")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("checkpoint config requires tenant_id")
    if not isinstance(thread_id, str) or not thread_id.startswith(f"{tenant_id}:"):
        raise ValueError("checkpoint thread_id does not match tenant_id")
    return tenant_id, thread_id


def _scope_config(config: RunnableConfig | None, tenant_id: str) -> RunnableConfig | None:
    if config is None:
        return None
    scoped = dict(config)
    scoped["configurable"] = {**config.get("configurable", {}), "tenant_id": tenant_id}
    return cast(RunnableConfig, scoped)


class TenantRlsCheckpointer(BaseCheckpointSaver[Any]):
    """Bind every Postgres saver operation to a transaction-local RLS tenant."""

    def __init__(self, saver: Any) -> None:
        super().__init__(serde=saver.serde)
        self.saver = saver
        self._lock = asyncio.Lock()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        tenant_id, _ = _tenant_config(config)
        async with self._lock, self.saver.conn.transaction():
            await self.saver.conn.execute(
                "SELECT set_config('pk_agent.tenant_id', %s, true)", (tenant_id,)
            )
            result = await self.saver.aget_tuple(config)
        if result is None:
            return None
        return result._replace(
            config=_scope_config(result.config, tenant_id),
            parent_config=_scope_config(result.parent_config, tenant_id),
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            raise ValueError("tenant-scoped checkpoint listing requires config")
        tenant_id, _ = _tenant_config(config)
        if before is not None:
            before_tenant, _ = _tenant_config(before)
            if before_tenant != tenant_id:
                raise ValueError("checkpoint before config crosses tenant boundary")
        async with self._lock, self.saver.conn.transaction():
            await self.saver.conn.execute(
                "SELECT set_config('pk_agent.tenant_id', %s, true)", (tenant_id,)
            )
            results = [
                item
                async for item in self.saver.alist(
                    config, filter=filter, before=before, limit=limit
                )
            ]
        for item in results:
            yield item._replace(
                config=_scope_config(item.config, tenant_id),
                parent_config=_scope_config(item.parent_config, tenant_id),
            )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        tenant_id, _ = _tenant_config(config)
        async with self._lock, self.saver.conn.transaction():
            await self.saver.conn.execute(
                "SELECT set_config('pk_agent.tenant_id', %s, true)", (tenant_id,)
            )
            result = await self.saver.aput(config, checkpoint, metadata, new_versions)
        return _scope_config(result, tenant_id) or result

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        tenant_id, _ = _tenant_config(config)
        async with self._lock, self.saver.conn.transaction():
            await self.saver.conn.execute(
                "SELECT set_config('pk_agent.tenant_id', %s, true)", (tenant_id,)
            )
            await self.saver.aput_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        tenant_id, separator, _ = thread_id.partition(":")
        if not separator or not tenant_id:
            raise ValueError("tenant-scoped thread_id is required")
        async with self._lock, self.saver.conn.transaction():
            await self.saver.conn.execute(
                "SELECT set_config('pk_agent.tenant_id', %s, true)", (tenant_id,)
            )
            await self.saver.adelete_thread(thread_id)

    def get_next_version(self, current: Any, channel: Any) -> Any:
        return self.saver.get_next_version(current, channel)


async def _enable_checkpoint_rls(saver: Any) -> None:
    expression = (
        "split_part(thread_id, ':', 1) = "
        "current_setting('pk_agent.tenant_id', true)"
    )
    for table in _RLS_TABLES:
        policy = f"pk_agent_{table}_tenant"
        await saver.conn.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        await saver.conn.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        exists = await saver.conn.execute(
            "SELECT 1 FROM pg_policies WHERE schemaname = current_schema() "
            "AND tablename = %s AND policyname = %s",
            (table, policy),
        )
        if await exists.fetchone() is None:
            await saver.conn.execute(
                f'CREATE POLICY "{policy}" ON "{table}" '
                f"USING ({expression}) WITH CHECK ({expression})"
            )


async def open_checkpointer(database_url: str) -> tuple[Any, Callable[[], Awaitable[None]]]:
    if database_url.startswith("postgres"):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        context = AsyncPostgresSaver.from_conn_string(database_url)
        saver = await context.__aenter__()
        setup = getattr(saver, "setup", None)
        if setup is not None:
            await setup()
        await _enable_checkpoint_rls(saver)

        async def close() -> None:
            await context.__aexit__(None, None, None)

        return TenantRlsCheckpointer(saver), close

    async def noop() -> None:
        return None

    return InMemorySaver(), noop

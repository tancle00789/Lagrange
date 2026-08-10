from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import WatchError

TaskRecord = dict[str, Any]


class TaskStore(Protocol):
    async def create(
        self, record: TaskRecord, *, idempotency_key: str | None = None
    ) -> tuple[TaskRecord, bool]: ...

    async def get(self, task_id: str) -> TaskRecord | None: ...

    async def update(
        self,
        task_id: str,
        changes: TaskRecord,
        *,
        expected_statuses: Iterable[str] | None = None,
    ) -> TaskRecord | None: ...

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[TaskRecord]: ...

    async def aclose(self) -> None: ...


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def create(
        self, record: TaskRecord, *, idempotency_key: str | None = None
    ) -> tuple[TaskRecord, bool]:
        async with self._lock:
            if idempotency_key:
                scope = (str(record["tenant_id"]), idempotency_key)
                existing_id = self._idempotency.get(scope)
                if existing_id is not None:
                    return dict(self._tasks[existing_id]), False
                self._idempotency[scope] = str(record["task_id"])
            self._tasks[str(record["task_id"])] = dict(record)
            return dict(record), True

    async def get(self, task_id: str) -> TaskRecord | None:
        async with self._lock:
            record = self._tasks.get(task_id)
            return dict(record) if record is not None else None

    async def update(
        self,
        task_id: str,
        changes: TaskRecord,
        *,
        expected_statuses: Iterable[str] | None = None,
    ) -> TaskRecord | None:
        allowed = set(expected_statuses) if expected_statuses is not None else None
        async with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if allowed is not None and record.get("status") not in allowed:
                return dict(record)
            record.update(changes)
            return dict(record)

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[TaskRecord]:
        async with self._lock:
            records = [
                dict(record) for record in self._tasks.values() if record["tenant_id"] == tenant_id
            ]
        records.sort(key=lambda record: record.get("created_at", 0), reverse=True)
        return records[:limit]

    async def aclose(self) -> None:
        return None


class RedisTaskStore:
    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "pk_agent",
        task_retention_seconds: int = 604_800,
        idempotency_ttl_seconds: int = 86_400,
    ) -> None:
        self.client = client
        self.key_prefix = key_prefix
        self.task_retention_seconds = task_retention_seconds
        self.idempotency_ttl_seconds = idempotency_ttl_seconds

    def _task_key(self, task_id: str) -> str:
        return f"{self.key_prefix}:task:{task_id}"

    def _tenant_key(self, tenant_id: str) -> str:
        return f"{self.key_prefix}:tenant:{tenant_id}:tasks"

    def _idempotency_key(self, tenant_id: str, key: str) -> str:
        digest = hashlib.sha256(key.encode()).hexdigest()
        return f"{self.key_prefix}:tenant:{tenant_id}:idempotency:{digest}"

    async def create(
        self, record: TaskRecord, *, idempotency_key: str | None = None
    ) -> tuple[TaskRecord, bool]:
        task_id = str(record["task_id"])
        tenant_id = str(record["tenant_id"])
        if idempotency_key:
            scope_key = self._idempotency_key(tenant_id, idempotency_key)
            claimed = await self.client.set(
                scope_key,
                task_id,
                ex=self.idempotency_ttl_seconds,
                nx=True,
            )
            if not claimed:
                existing_id = await self.client.get(scope_key)
                if existing_id:
                    existing = await self.get(str(existing_id))
                    if existing is not None:
                        return existing, False
                    await self.client.eval(
                        "if redis.call('get', KEYS[1]) == ARGV[1] then "
                        "return redis.call('del', KEYS[1]) else return 0 end",
                        1,
                        scope_key,
                        str(existing_id),
                    )
                    return await self.create(record, idempotency_key=idempotency_key)

        encoded = json.dumps(record, separators=(",", ":"))
        async with self.client.pipeline(transaction=True) as pipeline:
            pipeline.set(self._task_key(task_id), encoded, ex=self.task_retention_seconds)
            pipeline.zadd(self._tenant_key(tenant_id), {task_id: float(record["created_at"])})
            pipeline.expire(self._tenant_key(tenant_id), self.task_retention_seconds)
            await pipeline.execute()
        return dict(record), True

    async def get(self, task_id: str) -> TaskRecord | None:
        raw = await self.client.get(self._task_key(task_id))
        if raw is None:
            return None
        return dict(json.loads(raw))

    async def update(
        self,
        task_id: str,
        changes: TaskRecord,
        *,
        expected_statuses: Iterable[str] | None = None,
    ) -> TaskRecord | None:
        task_key = self._task_key(task_id)
        allowed = set(expected_statuses) if expected_statuses is not None else None
        while True:
            async with self.client.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(task_key)
                    raw = await pipeline.get(task_key)
                    if raw is None:
                        await pipeline.unwatch()
                        return None
                    record = dict(json.loads(raw))
                    if allowed is not None and record.get("status") not in allowed:
                        await pipeline.unwatch()
                        return record
                    record.update(changes)
                    pipeline.multi()
                    pipeline.set(
                        task_key,
                        json.dumps(record, separators=(",", ":")),
                        ex=self.task_retention_seconds,
                    )
                    await pipeline.execute()
                    return record
                except WatchError:
                    continue

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[TaskRecord]:
        tenant_key = self._tenant_key(tenant_id)
        task_ids = await self.client.zrevrange(tenant_key, 0, max(limit - 1, 0))
        if not task_ids:
            return []
        raw_records = await self.client.mget([self._task_key(str(task_id)) for task_id in task_ids])
        records: list[TaskRecord] = []
        stale: list[str] = []
        for task_id, raw in zip(task_ids, raw_records, strict=True):
            if raw is None:
                stale.append(str(task_id))
                continue
            record = dict(json.loads(raw))
            if record.get("tenant_id") == tenant_id:
                records.append(record)
        if stale:
            await self.client.zrem(tenant_key, *stale)
        return records

    async def aclose(self) -> None:
        close = getattr(self.client, "aclose", None)
        if close is not None:
            await close()
        else:  # pragma: no cover - compatibility with redis-py <5
            await self.client.close()

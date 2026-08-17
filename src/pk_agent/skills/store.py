from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pk_agent.skills.models import (
    ALLOWED_TRANSITIONS,
    EvalResult,
    SkillRecord,
    SkillStatus,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_versions (
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    eval_pass_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    approved_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    PRIMARY KEY (skill_id, version)
);
CREATE TABLE IF NOT EXISTS skill_eval_runs (
    run_id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class SkillStoreError(Exception):
    """Skill persistence failure."""


class SkillNotFoundError(SkillStoreError):
    """Requested skill version or candidate does not exist."""


class SkillTransitionError(SkillStoreError):
    """Illegal status transition."""


class SkillStore(Protocol):
    """Persistence boundary for skill records (owner: Skill Registry)."""

    async def put_record(self, record: SkillRecord) -> None: ...
    async def get_record(self, skill_id: str, version: str) -> SkillRecord: ...
    async def list_family(self, skill_id: str) -> list[SkillRecord]: ...
    async def list_approved_latest(self) -> list[SkillRecord]: ...
    async def list_by_status(self, status: SkillStatus) -> list[SkillRecord]: ...
    async def transition(
        self,
        skill_id: str,
        version: str,
        to_status: SkillStatus,
        *,
        approved_by: str | None = None,
        eval_pass_rate: float | None = None,
    ) -> SkillRecord: ...
    async def put_eval_run(
        self, run_id: str, skill_id: str, version: str, result: EvalResult
    ) -> None: ...
    async def close(self) -> None: ...


def validate_transition(record: SkillRecord, to_status: SkillStatus) -> None:
    if to_status not in ALLOWED_TRANSITIONS[record.status]:
        raise SkillTransitionError(
            f"skill {record.skill_id}@{record.version}: "
            f"{record.status} -> {to_status} is not allowed"
        )


class PgSkillStore:
    """Postgres-backed store. Owns its own connection pool; idempotent DDL."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def open(cls, database_url: str) -> PgSkillStore:
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(database_url, min_size=1, max_size=4, open=False)
        await pool.open()
        store = cls(pool)
        await store._ensure_schema()
        return store

    async def _ensure_schema(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(_SCHEMA)

    async def close(self) -> None:
        await self._pool.close()

    async def put_record(self, record: SkillRecord) -> None:
        payload = record.model_dump(mode="json")
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO skill_versions
                    (skill_id, version, status, payload, eval_pass_rate,
                     approved_by, created_at, approved_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (skill_id, version) DO UPDATE SET
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload,
                    eval_pass_rate = EXCLUDED.eval_pass_rate,
                    approved_by = EXCLUDED.approved_by,
                    approved_at = EXCLUDED.approved_at
                """,
                (
                    record.skill_id,
                    record.version,
                    record.status,
                    json.dumps(payload),
                    record.eval_pass_rate,
                    record.approved_by,
                    record.created_at,
                    record.approved_at,
                ),
            )

    def _row_to_record(self, row: Any) -> SkillRecord:
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return SkillRecord(**payload)

    async def get_record(self, skill_id: str, version: str) -> SkillRecord:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM skill_versions WHERE skill_id = %s AND version = %s",
                (skill_id, version),
            )
            row = await cursor.fetchone()
        if row is None:
            raise SkillNotFoundError(f"skill {skill_id}@{version} not found")
        return self._row_to_record(row)

    async def list_family(self, skill_id: str) -> list[SkillRecord]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM skill_versions WHERE skill_id = %s "
                "ORDER BY created_at DESC",
                (skill_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    async def list_approved_latest(self) -> list[SkillRecord]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT ON (skill_id) payload
                FROM skill_versions
                WHERE status = 'approved'
                ORDER BY skill_id, created_at DESC
                """
            )
            rows = await cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    async def list_by_status(self, status: SkillStatus) -> list[SkillRecord]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM skill_versions WHERE status = %s "
                "ORDER BY created_at DESC",
                (status,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    async def transition(
        self,
        skill_id: str,
        version: str,
        to_status: SkillStatus,
        *,
        approved_by: str | None = None,
        eval_pass_rate: float | None = None,
    ) -> SkillRecord:
        async with self._pool.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                "SELECT payload FROM skill_versions "
                "WHERE skill_id = %s AND version = %s FOR UPDATE",
                (skill_id, version),
            )
            row = await cursor.fetchone()
            if row is None:
                raise SkillNotFoundError(f"skill {skill_id}@{version} not found")
            record = self._row_to_record(row)
            validate_transition(record, to_status)

            update: dict[str, Any] = {"status": to_status}
            if eval_pass_rate is not None:
                update["eval_pass_rate"] = eval_pass_rate
            if to_status == "approved":
                if approved_by is None:
                    raise SkillTransitionError("approval requires approved_by")
                update["approved_by"] = approved_by
                update["approved_at"] = datetime.now(UTC).isoformat()
            payload = record.model_dump(mode="json") | update
            updated = SkillRecord(**payload)

            if to_status == "approved":
                # Publish = supersede: older approved versions are deprecated
                # in the same transaction.
                await conn.execute(
                    """
                    UPDATE skill_versions SET status = 'deprecated',
                        payload = payload || '{"status": "deprecated"}'::jsonb
                    WHERE skill_id = %s AND status = 'approved' AND version <> %s
                    """,
                    (skill_id, version),
                )
            await conn.execute(
                """
                UPDATE skill_versions SET status = %s, payload = %s,
                    eval_pass_rate = %s, approved_by = %s, approved_at = %s
                WHERE skill_id = %s AND version = %s
                """,
                (
                    updated.status,
                    json.dumps(updated.model_dump(mode="json")),
                    updated.eval_pass_rate,
                    updated.approved_by,
                    updated.approved_at,
                    skill_id,
                    version,
                ),
            )
        return updated

    async def put_eval_run(
        self, run_id: str, skill_id: str, version: str, result: EvalResult
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO skill_eval_runs (run_id, skill_id, version, result) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (run_id) DO UPDATE SET result = EXCLUDED.result",
                (run_id, skill_id, version, json.dumps(result.model_dump(mode="json"))),
            )


class InMemorySkillStore:
    """Deterministic store for unit tests and non-PG runtimes."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], SkillRecord] = {}
        self._eval_runs: dict[str, EvalResult] = {}

    async def put_record(self, record: SkillRecord) -> None:
        self._records[(record.skill_id, record.version)] = record

    async def get_record(self, skill_id: str, version: str) -> SkillRecord:
        try:
            return self._records[(skill_id, version)]
        except KeyError:
            raise SkillNotFoundError(f"skill {skill_id}@{version} not found") from None

    async def list_family(self, skill_id: str) -> list[SkillRecord]:
        records = [r for (sid, _), r in self._records.items() if sid == skill_id]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    async def list_approved_latest(self) -> list[SkillRecord]:
        latest: dict[str, SkillRecord] = {}
        for record in self._records.values():
            if record.status != "approved":
                continue
            current = latest.get(record.skill_id)
            if current is None or record.created_at > current.created_at:
                latest[record.skill_id] = record
        return list(latest.values())

    async def list_by_status(self, status: SkillStatus) -> list[SkillRecord]:
        records = [r for r in self._records.values() if r.status == status]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    async def transition(
        self,
        skill_id: str,
        version: str,
        to_status: SkillStatus,
        *,
        approved_by: str | None = None,
        eval_pass_rate: float | None = None,
    ) -> SkillRecord:
        record = await self.get_record(skill_id, version)
        validate_transition(record, to_status)
        update: dict[str, Any] = {"status": to_status}
        if eval_pass_rate is not None:
            update["eval_pass_rate"] = eval_pass_rate
        if to_status == "approved":
            if approved_by is None:
                raise SkillTransitionError("approval requires approved_by")
            update["approved_by"] = approved_by
            update["approved_at"] = datetime.now(UTC)
            for other in list(self._records.values()):
                if (
                    other.skill_id == skill_id
                    and other.version != version
                    and other.status == "approved"
                ):
                    self._records[(other.skill_id, other.version)] = (
                        other.model_copy(update={"status": "deprecated"})
                    )
        updated = record.model_copy(update=update)
        self._records[(skill_id, version)] = updated
        return updated

    async def put_eval_run(
        self, run_id: str, skill_id: str, version: str, result: EvalResult
    ) -> None:
        self._eval_runs[run_id] = result

    async def close(self) -> None:
        return None


SkillStoreFactory = Callable[[], Awaitable[SkillStore]]

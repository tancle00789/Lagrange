from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pk_agent.providers.contract import (
    KnowledgeChunk,
    KnowledgeHit,
    KnowledgeIngestionJob,
)


@runtime_checkable
class KnowledgeProvider(Protocol):
    async def search(
        self, query: str, *, tenant_id: str, limit: int = 10
    ) -> list[KnowledgeHit]: ...

    async def get_chunks(
        self, chunk_ids: list[str], *, tenant_id: str
    ) -> list[KnowledgeChunk]: ...

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str: ...

    async def get_ingestion_status(
        self, job_id: str, *, tenant_id: str
    ) -> KnowledgeIngestionJob: ...

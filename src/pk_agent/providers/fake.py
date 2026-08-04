from __future__ import annotations

from typing import Any

from pk_agent.providers.contract import (
    KnowledgeChunk,
    KnowledgeHit,
    KnowledgeIngestionJob,
    SourceReference,
)


class FakeKnowledgeProvider:
    """Deterministic in-memory provider for tests. Never performs network I/O."""

    def __init__(self, snippet_max_chars: int = 500) -> None:
        self.snippet_max_chars = snippet_max_chars

    async def search(
        self, query: str, *, tenant_id: str, limit: int = 10
    ) -> list[KnowledgeHit]:
        return [
            KnowledgeHit(
                chunk_id=f"fake-chunk-{tenant_id}-{index}",
                score=1.0 - index * 0.1,
                source=SourceReference(
                    kb_id="fake-kb",
                    document_id=f"fake-doc-{index}",
                    title="Fake Document",
                    chunk_index=index,
                ),
                snippet=f"fake snippet for: {query}"[: self.snippet_max_chars],
            )
            for index in range(min(limit, 2))
        ]

    async def get_chunks(
        self, chunk_ids: list[str], *, tenant_id: str
    ) -> list[KnowledgeChunk]:
        return [
            KnowledgeChunk(
                chunk_id=chunk_id,
                document_id="fake-doc",
                content=f"fake content for {chunk_id}"[: self.snippet_max_chars],
            )
            for chunk_id in chunk_ids
        ]

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        return f"fake-job-{tenant_id}"

    async def get_ingestion_status(
        self, job_id: str, *, tenant_id: str
    ) -> KnowledgeIngestionJob:
        return KnowledgeIngestionJob(job_id=job_id, status="completed")

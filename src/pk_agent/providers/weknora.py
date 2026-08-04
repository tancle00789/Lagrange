from __future__ import annotations

from typing import Any

from pk_agent.providers.contract import (
    KnowledgeChunk,
    KnowledgeHit,
    KnowledgeIngestionJob,
    SourceReference,
)
from pk_agent.providers.errors import KnowledgeBadRequest
from pk_agent.providers.weknora_client import WeKnoraClient


class WeKnoraKnowledgeProvider:
    """KnowledgeProvider backed by a real WeKnora subsystem.

    Owns the mapping from WeKnora raw JSON to internal contract models and
    truncates snippets so graph state can never receive large text blobs.
    """

    def __init__(
        self,
        client: WeKnoraClient,
        *,
        default_kb_id: str | None = None,
        kb_by_tenant: dict[str, str] | None = None,
        snippet_max_chars: int = 500,
    ) -> None:
        self.client = client
        self.default_kb_id = default_kb_id
        self.kb_by_tenant = kb_by_tenant or {}
        self.snippet_max_chars = snippet_max_chars

    def _kb_id(self, tenant_id: str) -> str:
        kb_id = self.kb_by_tenant.get(tenant_id) or self.default_kb_id
        if not kb_id:
            raise KnowledgeBadRequest(f"no knowledge base mapped for tenant {tenant_id!r}")
        return kb_id

    def _truncate(self, text: str) -> str:
        return text[: self.snippet_max_chars]

    async def search(
        self, query: str, *, tenant_id: str, limit: int = 10
    ) -> list[KnowledgeHit]:
        kb_id = self._kb_id(tenant_id)
        raw_results = await self.client.hybrid_search(kb_id, query, limit=limit)
        hits: list[KnowledgeHit] = []
        for raw in raw_results:
            chunk_id = str(raw.get("id") or "")
            if not chunk_id:
                continue
            hits.append(
                KnowledgeHit(
                    chunk_id=chunk_id,
                    score=float(raw.get("score") or 0.0),
                    source=SourceReference(
                        kb_id=str(raw.get("knowledge_base_id") or kb_id),
                        document_id=str(raw.get("knowledge_id") or ""),
                        title=str(
                            raw.get("knowledge_title") or raw.get("knowledge_filename") or ""
                        ),
                        chunk_index=int(raw.get("chunk_index") or 0),
                    ),
                    snippet=self._truncate(str(raw.get("content") or "")),
                )
            )
        return hits

    async def get_chunks(
        self, chunk_ids: list[str], *, tenant_id: str
    ) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        for chunk_id in chunk_ids:
            raw = await self.client.get_chunk(chunk_id)
            chunks.append(
                KnowledgeChunk(
                    chunk_id=str(raw.get("id") or chunk_id),
                    document_id=str(raw.get("knowledge_id") or ""),
                    content=self._truncate(str(raw.get("content") or "")),
                    metadata={
                        str(k): str(v)
                        for k, v in (raw.get("metadata") or {}).items()
                    },
                )
            )
        return chunks

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        kb_id = self._kb_id(tenant_id)
        filename = str(document.get("filename") or "document.txt")
        content = document.get("content")
        if isinstance(content, str):
            payload = content.encode("utf-8")
        elif isinstance(content, bytes):
            payload = content
        else:
            raise KnowledgeBadRequest("document must include string or bytes 'content'")
        raw = await self.client.upload_file(kb_id, filename=filename, content=payload)
        knowledge_id = str(raw.get("id") or "")
        if not knowledge_id:
            raise KnowledgeBadRequest("weknora upload returned no knowledge id")
        return knowledge_id

    async def get_ingestion_status(
        self, job_id: str, *, tenant_id: str
    ) -> KnowledgeIngestionJob:
        raw = await self.client.get_knowledge(job_id)
        return KnowledgeIngestionJob(
            job_id=job_id,
            status=str(raw.get("parse_status") or raw.get("status") or "unknown"),
            error=raw.get("error_message") or None,
        )

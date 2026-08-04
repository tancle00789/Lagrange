from __future__ import annotations

import asyncio
import logging
from typing import Any

from pk_agent.literature.base import LiteratureSource
from pk_agent.literature.ingest import fetch_and_ingest
from pk_agent.literature.models import LiteratureRecord
from pk_agent.providers.base import KnowledgeProvider

logger = logging.getLogger(__name__)


def make_literature_search_handler(
    sources: dict[str, LiteratureSource],
):
    """Build a ToolRegistry handler that fans out to all literature sources.

    A failing source is skipped and reported in `errors`; it never fails the
    whole call unless every source failed with zero records.
    """

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or "").strip()
        if not query:
            raise ValueError("literature_search requires a non-empty 'query'")
        limit = int(payload.get("limit") or 5)
        requested = payload.get("sources")
        if requested:
            unknown = [name for name in requested if name not in sources]
            if unknown:
                raise ValueError(f"unknown literature sources: {unknown}")
            names = [str(name) for name in requested]
        else:
            names = list(sources)
        results = await asyncio.gather(
            *(sources[name].search(query, limit=limit) for name in names),
            return_exceptions=True,
        )
        records: list[dict[str, Any]] = []
        errors: list[str] = []
        for name, result in zip(names, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("literature source %s failed: %s", name, result)
                errors.append(f"{name}: {type(result).__name__}: {result}")
                continue
            records.extend(record.model_dump(mode="json") for record in result)
        return {"records": records, "errors": errors}

    return handler


def make_literature_ingest_handler(
    *,
    provider: KnowledgeProvider,
    allowlist: set[str],
    max_bytes: int,
):
    """Build a ToolRegistry handler that downloads a record's PDF into WeKnora.

    Registered as risk L2 and only invoked when the task explicitly set
    ingest_external=True (ADR 006 copyright boundary).
    """

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        record = LiteratureRecord(**payload["record"])
        tenant_id = str(payload["tenant_id"])
        knowledge_id = await fetch_and_ingest(
            record,
            provider=provider,
            tenant_id=tenant_id,
            allowlist=allowlist,
            max_bytes=max_bytes,
        )
        return {"source_id": record.source_id, "knowledge_id": knowledge_id}

    return handler

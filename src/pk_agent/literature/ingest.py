from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx

from pk_agent.literature.errors import (
    LiteratureBadRequest,
    LiteratureTimeout,
    LiteratureUnavailable,
)
from pk_agent.literature.models import LiteratureRecord
from pk_agent.providers.base import KnowledgeProvider
from pk_agent.security.ssrf import validate_url

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 3
_CHUNK_BYTES = 64 * 1024


async def fetch_pdf(
    record: LiteratureRecord,
    *,
    allowlist: set[str],
    max_bytes: int,
    timeout_seconds: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """Download a record's PDF with SSRF validation and size/type limits."""
    if not record.pdf_url:
        raise LiteratureBadRequest(f"record {record.source_id} has no pdf_url")

    owns_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds), follow_redirects=False
    )
    try:
        url = validate_url(record.pdf_url, allowlist=allowlist)
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                response = await http.get(url)
            except httpx.TimeoutException as exc:
                raise LiteratureTimeout(f"pdf download timed out: {url}") from exc
            except httpx.HTTPError as exc:
                raise LiteratureUnavailable(f"pdf download failed: {url}: {exc}") from exc

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise LiteratureBadRequest(f"pdf redirect without location: {url}")
                url = validate_url(
                    urljoin(url, location),
                    allowlist=allowlist,
                )
                continue

            if response.status_code >= 400:
                raise LiteratureUnavailable(
                    f"pdf download got {response.status_code}: {url}"
                )
            content_type = response.headers.get("content-type", "").lower()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise LiteratureBadRequest(
                    f"pdf exceeds {max_bytes} bytes (content-length {content_length})"
                )
            if content_type and "pdf" not in content_type and "octet-stream" not in content_type:
                raise LiteratureBadRequest(f"unexpected pdf content-type: {content_type}")

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise LiteratureBadRequest(f"pdf exceeds {max_bytes} bytes while streaming")
                chunks.append(chunk)
            return b"".join(chunks)

        raise LiteratureBadRequest(f"pdf download exceeded {_MAX_REDIRECTS} redirects")
    finally:
        if owns_client:
            await http.aclose()


async def fetch_and_ingest(
    record: LiteratureRecord,
    *,
    provider: KnowledgeProvider,
    tenant_id: str,
    allowlist: set[str],
    max_bytes: int,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Download a record's PDF and ingest it into the knowledge subsystem.

    Only called when the task explicitly opted in (ingest_external=True);
    see ADR 006 for the copyright boundary.
    """
    pdf = await fetch_pdf(
        record, allowlist=allowlist, max_bytes=max_bytes, client=client
    )
    filename = f"{record.source_type}-{record.external_id.replace('/', '_')}.pdf"
    knowledge_id = await provider.ingest(
        {"filename": filename, "content": pdf}, tenant_id=tenant_id
    )
    logger.info(
        "ingested external literature %s as knowledge %s (tenant %s)",
        record.source_id,
        knowledge_id,
        tenant_id,
    )
    return knowledge_id

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pk_agent.literature.errors import LiteratureBadRequest
from pk_agent.literature.ingest import fetch_and_ingest, fetch_pdf
from pk_agent.literature.models import LiteratureRecord
from pk_agent.providers.contract import KnowledgeChunk, KnowledgeHit, KnowledgeIngestionJob
from pk_agent.security.ssrf import SSRFViolation, validate_url

ALLOWLIST = {"export.arxiv.org", "arxiv.org"}


def _record(pdf_url: str | None = "https://arxiv.org/pdf/2401.00001") -> LiteratureRecord:
    return LiteratureRecord(
        source_type="arxiv",
        external_id="2401.00001",
        title="Paper",
        url="https://arxiv.org/abs/2401.00001",
        pdf_url=pdf_url,
    )


class _RecordingProvider:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list[KnowledgeHit]:
        return []

    async def get_chunks(self, chunk_ids: list[str], *, tenant_id: str) -> list[KnowledgeChunk]:
        return []

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        self.documents.append(document)
        return "knowledge-1"

    async def get_ingestion_status(self, job_id: str, *, tenant_id: str) -> KnowledgeIngestionJob:
        return KnowledgeIngestionJob(job_id=job_id, status="completed")


def test_validate_url_rejects_http_and_unknown_hosts() -> None:
    with pytest.raises(SSRFViolation):
        validate_url("http://arxiv.org/pdf/x", allowlist=ALLOWLIST)
    with pytest.raises(SSRFViolation):
        validate_url("https://evil.example.com/x", allowlist=ALLOWLIST)
    assert validate_url("https://arxiv.org/pdf/x", allowlist=ALLOWLIST)


async def test_fetch_pdf_rejects_host_outside_allowlist() -> None:
    record = _record(pdf_url="https://evil.example.com/paper.pdf")
    with pytest.raises(SSRFViolation):
        await fetch_pdf(record, allowlist=ALLOWLIST, max_bytes=1000)


async def test_fetch_pdf_rejects_wrong_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LiteratureBadRequest, match="content-type"):
        await fetch_pdf(_record(), allowlist=ALLOWLIST, max_bytes=1000, client=client)


async def test_fetch_pdf_enforces_size_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"%PDF" + b"0" * 5000,
            headers={"content-type": "application/pdf"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LiteratureBadRequest, match="exceeds"):
        await fetch_pdf(_record(), allowlist=ALLOWLIST, max_bytes=100, client=client)


async def test_fetch_pdf_follows_redirect_within_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "arxiv.org":
            return httpx.Response(
                302, headers={"location": "https://export.arxiv.org/pdf/2401.00001"}
            )
        return httpx.Response(
            200, content=b"%PDF-1.4 body", headers={"content-type": "application/pdf"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pdf = await fetch_pdf(_record(), allowlist=ALLOWLIST, max_bytes=1000, client=client)
    assert pdf == b"%PDF-1.4 body"


async def test_fetch_pdf_rejects_redirect_outside_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.com/x.pdf"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SSRFViolation):
        await fetch_pdf(_record(), allowlist=ALLOWLIST, max_bytes=1000, client=client)


async def test_fetch_pdf_requires_pdf_url() -> None:
    with pytest.raises(LiteratureBadRequest, match="no pdf_url"):
        await fetch_pdf(_record(pdf_url=None), allowlist=ALLOWLIST, max_bytes=1000)


async def test_fetch_and_ingest_uploads_to_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF-1.4 data", headers={"content-type": "application/pdf"}
        )

    provider = _RecordingProvider()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    knowledge_id = await fetch_and_ingest(
        _record(),
        provider=provider,
        tenant_id="tenant-a",
        allowlist=ALLOWLIST,
        max_bytes=1000,
        client=client,
    )
    assert knowledge_id == "knowledge-1"
    assert provider.documents[0]["filename"] == "arxiv-2401.00001.pdf"
    assert provider.documents[0]["content"] == b"%PDF-1.4 data"

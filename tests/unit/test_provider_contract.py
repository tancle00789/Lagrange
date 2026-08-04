from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pk_agent.providers.base import KnowledgeProvider
from pk_agent.providers.errors import (
    KnowledgeAuthError,
    KnowledgeBadRequest,
    KnowledgeNotFound,
    KnowledgeTimeout,
    KnowledgeUnavailable,
)
from pk_agent.providers.fake import FakeKnowledgeProvider
from pk_agent.providers.weknora import WeKnoraKnowledgeProvider
from pk_agent.providers.weknora_client import WeKnoraClient

SEARCH_RESPONSE = {
    "success": True,
    "data": [
        {
            "id": "chunk-1",
            "content": "alpha " * 200,
            "knowledge_id": "doc-1",
            "knowledge_base_id": "kb-1",
            "knowledge_title": "Doc One",
            "chunk_index": 3,
            "score": 0.91,
        }
    ],
}


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _real_provider(handler, *, snippet_max_chars: int = 500) -> WeKnoraKnowledgeProvider:
    client = WeKnoraClient(
        "http://weknora-test",
        "test-key",
        client=httpx.AsyncClient(
            base_url="http://weknora-test", transport=_transport(handler)
        ),
    )
    return WeKnoraKnowledgeProvider(
        client, default_kb_id="kb-1", snippet_max_chars=snippet_max_chars
    )


def _providers() -> list[KnowledgeProvider]:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SEARCH_RESPONSE)

    return [FakeKnowledgeProvider(), _real_provider(ok_handler)]


@pytest.mark.parametrize("provider", _providers())
async def test_search_returns_contract_hits(provider: KnowledgeProvider) -> None:
    hits = await provider.search("query", tenant_id="tenant-a", limit=5)
    assert hits
    for hit in hits:
        assert hit.chunk_id
        assert hit.source.kb_id
        assert len(hit.snippet) <= 500


async def test_real_provider_truncates_snippets() -> None:
    provider = _real_provider(lambda req: httpx.Response(200, json=SEARCH_RESPONSE))
    hits = await provider.search("query", tenant_id="tenant-a")
    assert hits[0].snippet == ("alpha " * 200)[:500]


async def test_real_provider_maps_auth_error() -> None:
    provider = _real_provider(lambda req: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(KnowledgeAuthError):
        await provider.search("query", tenant_id="tenant-a")


async def test_real_provider_maps_not_found() -> None:
    provider = _real_provider(lambda req: httpx.Response(404, json={"error": "nope"}))
    with pytest.raises(KnowledgeNotFound):
        await provider.search("query", tenant_id="tenant-a")


async def test_real_provider_maps_bad_request() -> None:
    provider = _real_provider(lambda req: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(KnowledgeBadRequest):
        await provider.search("query", tenant_id="tenant-a")


async def test_real_provider_retries_5xx_then_fails() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, json={"error": "down"})

    client = WeKnoraClient(
        "http://weknora-test",
        "test-key",
        max_retries=3,
        client=httpx.AsyncClient(
            base_url="http://weknora-test", transport=_transport(handler)
        ),
    )
    provider = WeKnoraKnowledgeProvider(client, default_kb_id="kb-1")
    with pytest.raises(KnowledgeUnavailable):
        await provider.search("query", tenant_id="tenant-a")
    assert calls["count"] == 3


async def test_real_provider_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider = _real_provider(handler)
    with pytest.raises(KnowledgeTimeout):
        await provider.search("query", tenant_id="tenant-a")


async def test_real_provider_requires_kb_mapping() -> None:
    provider = WeKnoraKnowledgeProvider(
        _real_provider(lambda req: httpx.Response(200, json=SEARCH_RESPONSE)).client
    )
    with pytest.raises(KnowledgeBadRequest):
        await provider.search("query", tenant_id="unmapped-tenant")


async def test_get_chunks_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": "chunk-9",
                    "knowledge_id": "doc-9",
                    "content": "chunk body",
                    "metadata": {"page": "2"},
                },
            },
        )

    provider = _real_provider(handler)
    chunks = await provider.get_chunks(["chunk-9"], tenant_id="tenant-a")
    assert chunks[0].chunk_id == "chunk-9"
    assert chunks[0].document_id == "doc-9"
    assert chunks[0].metadata == {"page": "2"}


async def test_ingest_and_status_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/knowledge/file"):
            return httpx.Response(200, json={"success": True, "data": {"id": "doc-77"}})
        return httpx.Response(
            200,
            json={"success": True, "data": {"id": "doc-77", "parse_status": "completed"}},
        )

    provider = _real_provider(handler)
    job_id = await provider.ingest(
        {"filename": "a.txt", "content": "hello"}, tenant_id="tenant-a"
    )
    assert job_id == "doc-77"
    status = await provider.get_ingestion_status(job_id, tenant_id="tenant-a")
    assert status.status == "completed"


async def test_client_sends_api_key_header() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"success": True, "data": []})

    provider = _real_provider(handler)
    await provider.search("q", tenant_id="tenant-a")
    assert seen["key"] == "test-key"


async def test_search_request_payload() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"success": True, "data": []})

    client = WeKnoraClient(
        "http://weknora-test",
        "k",
        client=httpx.AsyncClient(
            base_url="http://weknora-test", transport=_transport(handler)
        ),
    )

    await client.hybrid_search("kb-1", "hello", limit=7)
    assert seen["path"] == "/api/v1/knowledge-bases/kb-1/hybrid-search"
    assert seen["body"] == {"query_text": "hello", "match_count": 7}

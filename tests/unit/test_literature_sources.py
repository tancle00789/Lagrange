from __future__ import annotations

import httpx
import pytest

from pk_agent.literature.arxiv import ArxivSource
from pk_agent.literature.base import SourceHttpClient
from pk_agent.literature.crossref import CrossRefSource
from pk_agent.literature.errors import (
    LiteratureAuthError,
    LiteratureBadRequest,
    LiteratureTimeout,
    LiteratureUnavailable,
)
from pk_agent.literature.fake import FakeLiteratureSource
from pk_agent.literature.github import GitHubSource
from pk_agent.literature.nature import NatureSource
from pk_agent.literature.tools import make_literature_search_handler

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>  Efficient   KV Cache Compression  </title>
    <summary>  We propose a method for
       KV cache compression. </summary>
    <published>2024-01-15T00:00:00Z</published>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Li</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2401.12345v2"/>
    <arxiv:doi>10.1234/test.2024</arxiv:doi>
    <arxiv:journal_ref>ICLR 2024</arxiv:journal_ref>
  </entry>
</feed>
"""


def _http(handler) -> SourceHttpClient:
    return SourceHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_arxiv_parses_entries() -> None:
    source = ArxivSource(
        http=_http(lambda req: httpx.Response(200, text=ARXIV_XML)),
        throttle=False,
    )
    records = await source.search("kv cache", limit=5)
    assert len(records) == 1
    record = records[0]
    assert record.source_type == "arxiv"
    assert record.external_id == "2401.12345v2"
    assert record.source_id == "arxiv:2401.12345v2"
    assert record.title == "Efficient KV Cache Compression"
    assert record.authors == ["Alice Zhang", "Bob Li"]
    assert record.pdf_url == "https://arxiv.org/pdf/2401.12345v2"
    assert record.doi == "10.1234/test.2024"
    assert record.venue == "ICLR 2024"
    assert record.published == "2024-01-15"


async def test_arxiv_invalid_xml_maps_bad_request() -> None:
    source = ArxivSource(http=_http(lambda req: httpx.Response(200, text="<feed")), throttle=False)
    with pytest.raises(LiteratureBadRequest):
        await source.search("x")


async def test_arxiv_timeout_maps_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    source = ArxivSource(http=_http(handler), throttle=False)
    with pytest.raises(LiteratureTimeout):
        await source.search("x")


async def test_arxiv_5xx_retries_then_unavailable() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, text="down")

    http = SourceHttpClient(
        max_retries=1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    source = ArxivSource(http=http, throttle=False)
    with pytest.raises(LiteratureUnavailable):
        await source.search("x")
    assert calls["count"] == 2


GITHUB_PAYLOAD = {
    "items": [
        {
            "full_name": "org/kv-cache",
            "name": "kv-cache",
            "owner": {"login": "org"},
            "description": "KV cache compression library",
            "html_url": "https://github.com/org/kv-cache",
            "created_at": "2024-02-01T00:00:00Z",
            "stargazers_count": 42,
            "language": "Python",
            "topics": ["llm", "kv-cache"],
        }
    ]
}


async def test_github_maps_repositories() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=GITHUB_PAYLOAD)

    source = GitHubSource(token="secret-token", http=_http(handler))  # noqa: S106
    records = await source.search("kv cache", limit=3)
    assert seen["auth"] == "Bearer secret-token"
    assert len(records) == 1
    record = records[0]
    assert record.source_id == "github:org/kv-cache"
    assert record.title == "kv-cache"
    assert record.authors == ["org"]
    assert record.extra["stars"] == 42
    assert record.extra["topics"] == ["llm", "kv-cache"]


async def test_github_auth_error() -> None:
    source = GitHubSource(http=_http(lambda req: httpx.Response(403, json={})))
    with pytest.raises(LiteratureAuthError):
        await source.search("x")


async def test_github_invalid_payload() -> None:
    source = GitHubSource(http=_http(lambda req: httpx.Response(200, json={"bad": True})))
    with pytest.raises(LiteratureBadRequest):
        await source.search("x")


CROSSREF_PAYLOAD = {
    "message": {
        "items": [
            {
                "DOI": "10.1126/science.abc123",
                "title": ["Attention Is All You Need"],
                "container-title": ["Science"],
                "publisher": "AAAS",
                "abstract": "<jats:p>We propose attention.</jats:p>",
                "issued": {"date-parts": [[2017, 6, 12]]},
                "URL": "https://doi.org/10.1126/science.abc123",
                "author": [{"given": "A", "family": "Vaswani"}],
            }
        ]
    }
}


async def test_crossref_maps_works() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["mailto"] = request.url.params.get("mailto", "")
        return httpx.Response(200, json=CROSSREF_PAYLOAD)

    source = CrossRefSource(mailto="me@example.com", http=_http(handler))
    records = await source.search("attention", limit=5)
    assert seen["mailto"] == "me@example.com"
    assert len(records) == 1
    record = records[0]
    assert record.source_id == "crossref:10.1126/science.abc123"
    assert record.venue == "Science"
    assert record.abstract == "We propose attention."
    assert record.published == "2017-6-12"
    assert record.authors == ["A Vaswani"]
    assert record.extra["publisher"] == "AAAS"


NATURE_PAYLOAD = {
    "records": [
        {
            "title": "Scaling laws for LLMs",
            "identifier": "doi:10.1038/s41586-024-0001",
            "doi": "10.1038/s41586-024-0001",
            "creators": [{"creator": "Carol Doe"}],
            "abstract": "We study scaling laws.",
            "publicationName": "Nature",
            "publicationDate": "2024-03-01",
            "url": [{"value": "https://link.springer.com/article/10.1038/s41586-024-0001"}],
        }
    ]
}


async def test_nature_maps_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("api_key") == "key-1"
        return httpx.Response(200, json=NATURE_PAYLOAD)

    source = NatureSource(api_key="key-1", http=_http(handler))
    records = await source.search("scaling laws", limit=5)
    assert len(records) == 1
    record = records[0]
    assert record.source_id == "nature:10.1038/s41586-024-0001"
    assert record.venue == "Nature"
    assert record.authors == ["Carol Doe"]


def test_nature_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        NatureSource(api_key="")


async def test_search_handler_aggregates_and_skips_failures() -> None:
    ok = FakeLiteratureSource()

    class _Broken:
        source_type = "broken"

        async def search(self, query: str, *, limit: int = 5):
            raise LiteratureUnavailable("down")

    handler = make_literature_search_handler({"ok": ok, "broken": _Broken()})
    result = await handler({"query": "kv cache", "limit": 3})
    assert len(result["records"]) == 1
    assert result["records"][0]["source_type"] == "arxiv"
    assert result["errors"] and "broken" in result["errors"][0]


async def test_search_handler_filters_sources() -> None:
    handler = make_literature_search_handler({"arxiv": FakeLiteratureSource()})
    result = await handler({"query": "q", "sources": ["arxiv"]})
    assert result["records"]
    with pytest.raises(ValueError, match="unknown literature sources"):
        await handler({"query": "q", "sources": ["nope"]})


async def test_search_handler_requires_query() -> None:
    handler = make_literature_search_handler({"arxiv": FakeLiteratureSource()})
    with pytest.raises(ValueError, match="query"):
        await handler({"query": "  "})

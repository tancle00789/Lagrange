from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET

from pk_agent.literature.base import SourceHttpClient
from pk_agent.literature.errors import LiteratureBadRequest
from pk_agent.literature.models import LiteratureRecord

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# arXiv asks for <= 1 request per 3 seconds from a single client.
_RATE_LIMIT_SECONDS = 3.0
_rate_lock = asyncio.Lock()
_last_request_at = 0.0


async def _throttle() -> None:
    global _last_request_at
    async with _rate_lock:
        wait = _RATE_LIMIT_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


class ArxivSource:
    source_type = "arxiv"

    def __init__(
        self,
        *,
        base_url: str = "https://export.arxiv.org",
        abstract_max_chars: int = 800,
        http: SourceHttpClient | None = None,
        throttle: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.abstract_max_chars = abstract_max_chars
        self._owns_http = http is None
        self.http = http or SourceHttpClient()
        self._throttle = throttle

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def search(self, query: str, *, limit: int = 5) -> list[LiteratureRecord]:
        if self._throttle:
            await _throttle()
        response = await self.http.get(
            f"{self.base_url}/api/query",
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": max(1, limit),
                "sortBy": "relevance",
            },
        )
        return self._parse(response.text)

    def _parse(self, body: str) -> list[LiteratureRecord]:
        try:
            root = SafeET.fromstring(body)
        except ET.ParseError as exc:
            raise LiteratureBadRequest(f"arxiv returned invalid XML: {exc}") from exc

        records: list[LiteratureRecord] = []
        for entry in root.findall(f"{_ATOM_NS}entry"):
            entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
            if not entry_id:
                continue
            external_id = entry_id.rsplit("/abs/", 1)[-1]
            title = " ".join((entry.findtext(f"{_ATOM_NS}title") or "").split())
            abstract = " ".join((entry.findtext(f"{_ATOM_NS}summary") or "").split())
            published = (entry.findtext(f"{_ATOM_NS}published") or "")[:10]
            authors = [
                name.strip()
                for author in entry.findall(f"{_ATOM_NS}author")
                if (name := author.findtext(f"{_ATOM_NS}name"))
            ]
            pdf_url = None
            for link in entry.findall(f"{_ATOM_NS}link"):
                if link.get("title") == "pdf" and link.get("href"):
                    pdf_url = link.get("href")
            doi = entry.findtext(f"{_ARXIV_NS}doi") or None
            journal_ref = entry.findtext(f"{_ARXIV_NS}journal_ref") or ""
            records.append(
                LiteratureRecord(
                    source_type="arxiv",
                    external_id=external_id,
                    title=title,
                    authors=authors,
                    abstract=abstract[: self.abstract_max_chars],
                    url=entry_id,
                    pdf_url=pdf_url,
                    doi=doi,
                    venue=journal_ref,
                    published=published,
                )
            )
        return records

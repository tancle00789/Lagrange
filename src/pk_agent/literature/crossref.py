from __future__ import annotations

import re

from pk_agent.literature.base import SourceHttpClient
from pk_agent.literature.errors import LiteratureBadRequest
from pk_agent.literature.models import LiteratureRecord

_TAG_RE = re.compile(r"<[^>]+>")


class CrossRefSource:
    """CrossRef works metadata (Science/AAAS family has metadata but no full-text API)."""

    source_type = "crossref"

    def __init__(
        self,
        *,
        base_url: str = "https://api.crossref.org",
        mailto: str = "",
        abstract_max_chars: int = 800,
        http: SourceHttpClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mailto = mailto
        self.abstract_max_chars = abstract_max_chars
        self._owns_http = http is None
        self.http = http or SourceHttpClient()

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def search(self, query: str, *, limit: int = 5) -> list[LiteratureRecord]:
        params: dict[str, object] = {"query": query, "rows": max(1, limit)}
        if self.mailto:
            params["mailto"] = self.mailto
        response = await self.http.get(f"{self.base_url}/works", params=params)
        try:
            body = response.json()
        except ValueError as exc:
            raise LiteratureBadRequest("crossref returned non-JSON body") from exc
        items = ((body.get("message") or {}).get("items"))
        if not isinstance(items, list):
            raise LiteratureBadRequest("crossref search returned invalid payload")

        records: list[LiteratureRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            doi = item.get("DOI")
            title_list = item.get("title") or []
            title = title_list[0] if title_list else ""
            if not doi or not title:
                continue
            venue_list = item.get("container-title") or []
            abstract = _TAG_RE.sub("", item.get("abstract") or "")
            abstract = " ".join(abstract.split())
            issued = (item.get("issued") or {}).get("date-parts") or []
            published = ""
            if issued and issued[0]:
                parts = [str(p) for p in issued[0]]
                published = "-".join(parts)
            authors = []
            for author in item.get("author") or []:
                name = " ".join(
                    p for p in (author.get("given"), author.get("family")) if p
                )
                if name:
                    authors.append(name)
            records.append(
                LiteratureRecord(
                    source_type="crossref",
                    external_id=str(doi),
                    title=title,
                    authors=authors,
                    abstract=abstract[: self.abstract_max_chars],
                    url=item.get("URL") or f"https://doi.org/{doi}",
                    doi=str(doi),
                    venue=venue_list[0] if venue_list else "",
                    published=published,
                    extra={"publisher": item.get("publisher") or ""},
                )
            )
        return records

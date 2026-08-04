from __future__ import annotations

from pk_agent.literature.base import SourceHttpClient
from pk_agent.literature.errors import LiteratureBadRequest
from pk_agent.literature.models import LiteratureRecord


class NatureSource:
    """Springer Nature Meta API (Nature family metadata + abstracts)."""

    source_type = "nature"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.springernature.com",
        abstract_max_chars: int = 800,
        http: SourceHttpClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("NatureSource requires a Springer Nature API key")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.abstract_max_chars = abstract_max_chars
        self._owns_http = http is None
        self.http = http or SourceHttpClient()

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def search(self, query: str, *, limit: int = 5) -> list[LiteratureRecord]:
        response = await self.http.get(
            f"{self.base_url}/meta/v2/json",
            params={
                "q": query,
                "s": 1,
                "p": max(1, limit),
                "api_key": self.api_key,
            },
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise LiteratureBadRequest("springer nature returned non-JSON body") from exc
        items = body.get("records")
        if not isinstance(items, list):
            raise LiteratureBadRequest("springer nature returned invalid payload")

        records: list[LiteratureRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            identifier = item.get("doi") or item.get("identifier")
            if not title or not identifier:
                continue
            external_id = str(identifier).removeprefix("doi:")
            authors = [
                c.get("creator")
                for c in item.get("creators") or []
                if c.get("creator")
            ]
            urls = [u.get("value") for u in item.get("url") or [] if u.get("value")]
            abstract = " ".join((item.get("abstract") or "").split())
            records.append(
                LiteratureRecord(
                    source_type="nature",
                    external_id=external_id,
                    title=title,
                    authors=authors,
                    abstract=abstract[: self.abstract_max_chars],
                    url=urls[0] if urls else "",
                    doi=item.get("doi"),
                    venue=item.get("publicationName") or "",
                    published=item.get("publicationDate") or "",
                )
            )
        return records

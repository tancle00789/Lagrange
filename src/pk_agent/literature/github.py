from __future__ import annotations

from pk_agent.literature.base import SourceHttpClient
from pk_agent.literature.errors import LiteratureBadRequest
from pk_agent.literature.models import LiteratureRecord


class GitHubSource:
    source_type = "github"

    def __init__(
        self,
        *,
        base_url: str = "https://api.github.com",
        token: str | None = None,
        abstract_max_chars: int = 800,
        http: SourceHttpClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.abstract_max_chars = abstract_max_chars
        self._owns_http = http is None
        self.http = http or SourceHttpClient()

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def search(self, query: str, *, limit: int = 5) -> list[LiteratureRecord]:
        response = await self.http.get(
            f"{self.base_url}/search/repositories",
            params={"q": query, "per_page": max(1, limit)},
            headers=self._headers(),
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise LiteratureBadRequest("github returned non-JSON body") from exc
        items = body.get("items")
        if not isinstance(items, list):
            raise LiteratureBadRequest("github search returned invalid payload")

        records: list[LiteratureRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            full_name = item.get("full_name")
            if not full_name:
                continue
            owner = item.get("owner") or {}
            owner_login = owner.get("login")
            records.append(
                LiteratureRecord(
                    source_type="github",
                    external_id=str(full_name),
                    title=item.get("name") or str(full_name),
                    authors=[str(owner_login)] if owner_login else [],
                    abstract=(item.get("description") or "")[: self.abstract_max_chars],
                    url=item.get("html_url") or "",
                    published=(item.get("created_at") or "")[:10],
                    venue="GitHub",
                    extra={
                        "stars": item.get("stargazers_count") or 0,
                        "language": item.get("language") or "",
                        "topics": item.get("topics") or [],
                    },
                )
            )
        return records

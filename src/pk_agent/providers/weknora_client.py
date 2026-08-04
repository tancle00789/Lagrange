from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pk_agent.providers.errors import (
    KnowledgeAuthError,
    KnowledgeBadRequest,
    KnowledgeNotFound,
    KnowledgeProviderError,
    KnowledgeTimeout,
    KnowledgeUnavailable,
)

logger = logging.getLogger(__name__)


class _RetryableUpstream(Exception):
    """Internal marker for connection errors and 5xx responses."""


class WeKnoraClient:
    """Thin async transport for the WeKnora HTTP API.

    Only this module knows WeKnora URLs, auth headers and response envelopes.
    All failures are mapped to pk_agent.providers.errors types; nothing is
    swallowed silently.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        self.client.headers["X-API-Key"] = api_key

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def hybrid_search(
        self, kb_id: str, query: str, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        payload = {"query_text": query, "match_count": limit}
        body = await self._request(
            "POST", f"/api/v1/knowledge-bases/{kb_id}/hybrid-search", json=payload
        )
        data = body.get("data") or []
        if not isinstance(data, list):
            raise KnowledgeUnavailable("hybrid-search returned non-list data")
        return [item for item in data if isinstance(item, dict)]

    async def get_chunk(self, chunk_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"/api/v1/chunks/by-id/{chunk_id}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise KnowledgeUnavailable(f"chunk {chunk_id} returned invalid payload")
        return data

    async def upload_file(
        self, kb_id: str, *, filename: str, content: bytes, content_type: str = "text/plain"
    ) -> dict[str, Any]:
        body = await self._request(
            "POST",
            f"/api/v1/knowledge-bases/{kb_id}/knowledge/file",
            files={"file": (filename, content, content_type)},
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise KnowledgeUnavailable("knowledge upload returned invalid payload")
        return data

    async def get_knowledge(self, knowledge_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"/api/v1/knowledge/{knowledge_id}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise KnowledgeUnavailable(f"knowledge {knowledge_id} returned invalid payload")
        return data

    async def health(self) -> bool:
        try:
            response = await self.client.get("/health")
        except httpx.HTTPError:
            return False
        return response.status_code < 500

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        @retry(
            retry=retry_if_exception_type(_RetryableUpstream),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=0.5, max=8),
            reraise=True,
        )
        async def _call() -> httpx.Response:
            try:
                response = await self.client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                raise KnowledgeTimeout(f"{method} {path} timed out") from exc
            except httpx.HTTPError as exc:
                raise _RetryableUpstream(f"{method} {path} connection error: {exc}") from exc
            if response.status_code >= 500:
                raise _RetryableUpstream(f"{method} {path} upstream {response.status_code}")
            return response

        started = method, path
        try:
            response = await _call()
        except _RetryableUpstream as exc:
            logger.warning("weknora request failed after retries: %s %s", *started)
            raise KnowledgeUnavailable(str(exc)) from exc

        logger.info("weknora %s %s -> %d", method, path, response.status_code)
        if response.status_code in (401, 403):
            raise KnowledgeAuthError(f"{method} {path} rejected: {response.status_code}")
        if response.status_code == 404:
            raise KnowledgeNotFound(f"{method} {path} not found")
        if response.status_code >= 400:
            raise KnowledgeBadRequest(f"{method} {path} bad request: {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise KnowledgeUnavailable(f"{method} {path} returned non-JSON body") from exc
        if not isinstance(body, dict):
            raise KnowledgeUnavailable(f"{method} {path} returned unexpected envelope")
        if body.get("success") is False:
            raise KnowledgeProviderError(f"{method} {path} upstream error: {body.get('error')}")
        return body

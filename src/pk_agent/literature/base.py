from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pk_agent.literature.errors import (
    LiteratureAuthError,
    LiteratureBadRequest,
    LiteratureTimeout,
    LiteratureUnavailable,
)
from pk_agent.literature.models import LiteratureRecord

logger = logging.getLogger(__name__)


@runtime_checkable
class LiteratureSource(Protocol):
    source_type: str

    async def search(self, query: str, *, limit: int = 5) -> list[LiteratureRecord]: ...

    async def aclose(self) -> None: ...


class _RetryableUpstream(Exception):
    """Internal marker for connection errors and 5xx responses."""


class SourceHttpClient:
    """Thin async GET transport shared by literature sources.

    Maps all failures to pk_agent.literature.errors types; nothing is
    swallowed silently. Retries only connection errors and 5xx.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries + 1)
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        @retry(
            retry=retry_if_exception_type(_RetryableUpstream),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=0.5, max=8),
            reraise=True,
        )
        async def _call() -> httpx.Response:
            try:
                response = await self.client.get(url, params=params, headers=headers)
            except httpx.TimeoutException as exc:
                raise LiteratureTimeout(f"GET {url} timed out") from exc
            except httpx.HTTPError as exc:
                raise _RetryableUpstream(f"GET {url} connection error: {exc}") from exc
            if response.status_code >= 500:
                raise _RetryableUpstream(f"GET {url} upstream {response.status_code}")
            return response

        try:
            response = await _call()
        except _RetryableUpstream as exc:
            logger.warning("literature request failed after retries: %s", url)
            raise LiteratureUnavailable(str(exc)) from exc

        logger.info("literature GET %s -> %d", url, response.status_code)
        if response.status_code in (401, 403):
            raise LiteratureAuthError(f"GET {url} rejected: {response.status_code}")
        if response.status_code == 429:
            raise LiteratureUnavailable(f"GET {url} rate limited")
        if response.status_code >= 400:
            raise LiteratureBadRequest(f"GET {url} bad request: {response.status_code}")
        return response

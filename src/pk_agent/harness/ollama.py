from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class OllamaModelError(Exception):
    """Ollama upstream call failed."""


class OllamaModel:
    """Chat model served by a local Ollama daemon (OpenAI-free path)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(timeout_seconds)
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def generate(self, prompt: str, *, capability: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # Structured-output consumers parse the raw content; reasoning
            # traces (qwen3 etc.) must not leak into it. Ignored by models
            # without thinking support.
            "think": False,
        }
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                response = await self.client.post("/api/chat", json=payload)
                if response.status_code >= 400:
                    raise OllamaModelError(
                        f"ollama chat failed: {response.status_code} {response.text[:200]}"
                    )
                body = response.json()
                content = (body.get("message") or {}).get("content")
                if not isinstance(content, str) or not content.strip():
                    raise OllamaModelError("ollama chat returned empty content")
                return content
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                logger.warning("ollama attempt %d failed: %s", attempt, type(exc).__name__)
        raise OllamaModelError(f"ollama unreachable: {last_error}") from last_error

from __future__ import annotations


class KnowledgeProviderError(Exception):
    """Base error for knowledge provider failures."""


class KnowledgeUnavailable(KnowledgeProviderError):
    """Upstream is unreachable or returned 5xx after retries."""


class KnowledgeTimeout(KnowledgeProviderError):
    """Upstream call exceeded the configured timeout."""


class KnowledgeAuthError(KnowledgeProviderError):
    """Authentication/authorization failed (401/403). Indicates misconfiguration."""


class KnowledgeBadRequest(KnowledgeProviderError):
    """Upstream rejected the request (4xx other than auth)."""


class KnowledgeNotFound(KnowledgeProviderError):
    """Requested resource does not exist upstream (404)."""

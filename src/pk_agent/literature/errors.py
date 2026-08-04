from __future__ import annotations


class LiteratureError(Exception):
    """Base error for literature source failures."""


class LiteratureUnavailable(LiteratureError):
    """Source is unreachable or returned 5xx after retries."""


class LiteratureTimeout(LiteratureError):
    """Source call exceeded the configured timeout."""


class LiteratureAuthError(LiteratureError):
    """Authentication/authorization failed (401/403). Indicates misconfiguration."""


class LiteratureBadRequest(LiteratureError):
    """Source rejected the request or returned an unparseable payload."""

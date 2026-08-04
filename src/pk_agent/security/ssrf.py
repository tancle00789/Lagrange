from __future__ import annotations

from urllib.parse import urlparse


class SSRFViolation(Exception):
    """URL failed SSRF validation (scheme/host not allowed)."""


def validate_url(url: str, *, allowlist: set[str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SSRFViolation(f"url scheme must be https: {url}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SSRFViolation(f"url has no host: {url}")
    allowed = {item.lower() for item in allowlist}
    if host not in allowed:
        raise SSRFViolation(f"host {host} not in SSRF allowlist")
    return url

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_model_noise(raw: str) -> str:
    """Remove reasoning traces and code fences from model output."""
    return _FENCE_RE.sub("", _THINK_RE.sub("", raw).strip()).strip()


def load_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object, tolerating prose around the outermost braces."""
    try:
        body = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            body = json.loads(text[start : end + 1])
        except ValueError:
            return None
    return body if isinstance(body, dict) else None

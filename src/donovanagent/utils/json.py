from __future__ import annotations

import json
import re
from typing import Any


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def loads_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def extract_marked_json_object(text: str) -> dict[str, Any] | None:
    """Return a JSON object only when the model clearly emitted a JSON block."""
    stripped = text.strip()
    direct = loads_object(stripped)
    if direct is not None:
        return direct
    match = _FENCED_JSON_RE.search(text)
    if not match:
        return None
    return loads_object(match.group(1).strip())

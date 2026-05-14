from __future__ import annotations


def derive_title(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return "New chat"
    return cleaned[:60] + ("..." if len(cleaned) > 60 else "")

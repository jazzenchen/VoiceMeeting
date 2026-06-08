from __future__ import annotations

import re


def clean_inline_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = re.sub(r"([A-Za-z0-9])\1{7,}", r"\1", cleaned)
    return cleaned


def strip_markdown_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    fence = re.search(r"```(?:markdown|md)?\s*(.*?)```", cleaned, flags=re.S | re.I)
    if fence:
        return fence.group(1).strip()
    return cleaned


def shorten_text(text: str, limit: int = 72) -> str:
    cleaned = clean_inline_text(text).strip("，,。；; ")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip("，,。；; ") + "..."

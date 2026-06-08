from __future__ import annotations

import re
from typing import Any, Dict, List

from .text_cleanup import clean_inline_text, shorten_text, strip_markdown_fence
from .transcript import is_unrecognized_text


GENERIC_MEETING_TITLES = {
    "今天的会议",
    "新会议",
    "新会议标题",
    "untitled meeting",
    "meeting",
    "会议纪要",
    "导入音频",
    "导入音视频",
}


def is_generic_meeting_title(title: Any) -> bool:
    cleaned = clean_inline_text(str(title or "")).strip(" #「」\"'“”‘’")
    return not cleaned or cleaned.lower() in GENERIC_MEETING_TITLES


def clean_generated_title(title: Any, limit: int = 20) -> str:
    cleaned = clean_inline_text(str(title or ""))
    cleaned = strip_markdown_fence(cleaned)
    cleaned = re.sub(r"^#+\s*", "", cleaned).strip()
    cleaned = re.sub(r"^(会议标题|标题)\s*[:：]\s*", "", cleaned).strip()
    cleaned = cleaned.strip(" #「」\"'“”‘’《》[]【】")
    if not cleaned or is_generic_meeting_title(cleaned):
        return ""
    cleaned = re.sub(r"(会议纪要|会议|纪要)$", "", cleaned).strip()
    cleaned = re.sub(r"[。.!！?？,，;；:：、]+$", "", cleaned).strip()
    if not cleaned or is_generic_meeting_title(cleaned):
        return ""
    return cleaned[:limit].strip()


def retitle_final_markdown(markdown: str, title: str) -> str:
    clean_title = clean_generated_title(title)
    if not clean_title:
        return markdown
    text = str(markdown or "").strip()
    if re.match(r"^#\s+.+$", text, flags=re.M):
        text = re.sub(r"^#\s+.+$", f"# {clean_title}", text, count=1, flags=re.M)
    else:
        text = f"# {clean_title}\n\n{text}"
    return text.rstrip() + "\n"


def notes_only_markdown(markdown: str) -> str:
    cleaned = strip_markdown_fence(markdown)
    cleaned = re.sub(r"\n+##\s*原始转写\s*.*$", "", cleaned, flags=re.S)
    return cleaned.strip()


def _candidate_sentences(items: List[Dict[str, Any]], max_items: int = 80) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()
    for item in items[:max_items]:
        if is_unrecognized_text(item.get("text")) or is_unrecognized_text(item.get("raw_text")):
            continue
        text = clean_inline_text(item.get("text") or item.get("raw_text") or "")
        if not text:
            continue
        parts = re.split(r"(?<=[。！？!?；;])\s*", text)
        if len(parts) == 1 and len(text) > 120:
            parts = [text[index : index + 90] for index in range(0, len(text), 90)]
        for part in parts:
            sentence = clean_inline_text(part).strip("，,。；; ")
            if len(sentence) < 8:
                continue
            key = sentence[:80]
            if key in seen:
                continue
            seen.add(key)
            candidates.append(sentence)
    return candidates


def local_title_from_content(meeting: Dict[str, Any], markdown: str = "") -> str:
    candidates: List[str] = []
    for line in notes_only_markdown(markdown).splitlines():
        cleaned = clean_inline_text(line)
        if not cleaned or cleaned.startswith("#") or cleaned.startswith("- 时间"):
            continue
        cleaned = re.sub(r"^[-*]\s+", "", cleaned).strip()
        if cleaned in {"暂无", "无", "待确认"} or cleaned.endswith("："):
            continue
        candidates.append(cleaned)

    if not candidates:
        candidates = _candidate_sentences(meeting.get("utterances") or meeting.get("segments", []))

    for candidate in candidates:
        title = clean_generated_title(shorten_text(candidate, 20))
        if title:
            return title
    return ""

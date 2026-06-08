from __future__ import annotations

from typing import Any

from .summarizer import (
    MeetingSummarizer,
    is_generic_meeting_title,
    local_title_from_content,
    retitle_final_markdown,
)


async def prepare_final_markdown_for_storage(
    store: Any,
    summarizer: MeetingSummarizer,
    meeting_id: str,
    markdown: str,
    force_local_title: bool = False,
) -> str:
    try:
        latest = store.get_meeting(meeting_id)
    except KeyError:
        return markdown
    if not is_generic_meeting_title(latest.get("title")):
        return markdown

    if force_local_title:
        generated_title = local_title_from_content(latest, markdown)
    else:
        generated_title = await summarizer.generate_title(latest, markdown)
    if not generated_title:
        return markdown

    try:
        latest = store.get_meeting(meeting_id)
        if is_generic_meeting_title(latest.get("title")):
            store.update_meeting_title(
                meeting_id,
                generated_title,
                latest.get("description") or "",
            )
    except Exception:
        return markdown
    return retitle_final_markdown(markdown, generated_title)

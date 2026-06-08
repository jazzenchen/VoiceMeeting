import unittest

from backend.final_notes import prepare_final_markdown_for_storage


class FakeStore:
    def __init__(self, title: str) -> None:
        self.meeting = {"id": "m1", "title": title, "description": "", "segments": []}
        self.updated_titles = []

    def get_meeting(self, meeting_id: str):
        if meeting_id != "m1":
            raise KeyError(meeting_id)
        return dict(self.meeting)

    def update_meeting_title(self, meeting_id: str, title: str, description: str = ""):
        self.updated_titles.append(title)
        self.meeting["title"] = title
        self.meeting["description"] = description
        return dict(self.meeting)


class FakeSummarizer:
    async def generate_title(self, meeting, markdown: str = "") -> str:
        return "AI 方案评审"


class FinalNotesTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_final_markdown_updates_generic_title(self) -> None:
        store = FakeStore("今天的会议")
        markdown = "# 今天的会议\n\n## 会议摘要\n- 讨论 AI 方案。"

        result = await prepare_final_markdown_for_storage(store, FakeSummarizer(), "m1", markdown)

        self.assertEqual(store.updated_titles, ["AI 方案评审"])
        self.assertEqual(result.splitlines()[0], "# AI 方案评审")

    async def test_prepare_final_markdown_keeps_custom_title(self) -> None:
        store = FakeStore("客户复盘")
        markdown = "# 客户复盘\n\n## 会议摘要\n- 讨论 AI 方案。"

        result = await prepare_final_markdown_for_storage(store, FakeSummarizer(), "m1", markdown)

        self.assertEqual(store.updated_titles, [])
        self.assertEqual(result, markdown)


if __name__ == "__main__":
    unittest.main()

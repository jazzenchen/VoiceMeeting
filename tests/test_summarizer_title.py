import unittest

from backend.summarizer import (
    clean_generated_title,
    is_generic_meeting_title,
    local_title_from_content,
    retitle_final_markdown,
)


class SummarizerTitleTests(unittest.TestCase):
    def test_generic_titles_are_detected(self) -> None:
        self.assertTrue(is_generic_meeting_title("今天的会议"))
        self.assertTrue(is_generic_meeting_title("Untitled Meeting"))
        self.assertTrue(is_generic_meeting_title("导入音视频"))
        self.assertFalse(is_generic_meeting_title("AI 方案评审"))

    def test_clean_generated_title_limits_length_and_strips_noise(self) -> None:
        self.assertEqual(clean_generated_title("标题：AI 方案评审会议纪要", limit=8), "AI 方案评审")
        self.assertEqual(clean_generated_title("# 今天的会议"), "")

    def test_retitle_final_markdown_updates_top_heading(self) -> None:
        markdown = "# 今天的会议\n\n## 会议摘要\n- 讨论 AI 方案。"

        self.assertEqual(
            retitle_final_markdown(markdown, "AI 方案评审"),
            "# AI 方案评审\n\n## 会议摘要\n- 讨论 AI 方案。\n",
        )

    def test_local_title_uses_notes_content(self) -> None:
        title = local_title_from_content(
            {"segments": []},
            "# 今天的会议\n\n## 会议摘要\n- 讨论客户 AI 自动化方案落地计划。",
        )

        self.assertEqual(title, "讨论客户 AI 自动化方案落地计划")


if __name__ == "__main__":
    unittest.main()

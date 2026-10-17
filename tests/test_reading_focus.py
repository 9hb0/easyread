"""Reading batches need bounded paper context without expanding their evidence scope."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from easyread import prompts
from easyread.store import Workspace, write_json_atomic


class ReadingFocusTest(unittest.TestCase):
    def paper(self):
        return {
            "meta": {"title_en": "Training instead of tool use"},
            "translation": {"done_pages": [1, 2, 3, 4]},
            "blocks": [
                {"id": "abstract", "page": 1, "type": "para", "role": "abstract",
                 "zh": "训练时学会规划，推理时不调用外部工具。", "en": "Train planning without inference tools."},
                {"id": "intro", "page": 1, "type": "heading", "zh": "引言"},
                {"id": "method", "page": 2, "type": "heading", "num": "3", "zh": "方法"},
                {"id": "detail", "page": 3, "type": "para", "zh": "本批固定译文。", "en": "Fixed source."},
                {"id": "results", "page": 4, "type": "heading", "zh": "实验结果"},
                {"id": "future", "page": 5, "type": "heading", "zh": "未翻译的章节"},
            ],
        }

    def focus(self, paper, pages):
        helper = getattr(prompts, "_reading_focus", None)
        self.assertTrue(callable(helper), "精读缺少全文背景，无法判断本批信息在全文中的位置")
        result = helper(paper, pages)
        return result, json.loads(result.split("背景数据：\n", 1)[1])

    def test_batch_inherits_section_from_previous_page_and_sees_paper_mainline(self):
        paper = self.paper()
        original = copy.deepcopy(paper)
        text, data = self.focus(paper, [3])
        self.assertEqual(data["abstract"], "训练时学会规划，推理时不调用外部工具。")
        self.assertEqual(data["current_sections"], [{"page": 2, "title": "3 方法"}])
        self.assertEqual([r["title"] for r in data["outline"]], ["引言", "3 方法", "实验结果"])
        self.assertNotIn("未翻译的章节", text)
        self.assertEqual(paper, original)

    def test_enrich_keeps_background_out_of_fixed_batch_and_reference_scope(self):
        paper = self.paper()
        with tempfile.TemporaryDirectory() as folder:
            ws = Workspace(Path(folder))
            write_json_atomic(ws.root / "paper.json", paper)
            prompt = prompts.enrich(ws, [3])
            material = json.loads(prompt.split("原文与固定译文：\n", 1)[1])
            self.assertEqual([b["id"] for b in material], ["detail"])
            self.assertIn("训练时学会规划，推理时不调用外部工具。", prompt)
            self.assertIn("不得作为本批 source_refs", prompt)
            self.assertEqual(ws.load("paper")["blocks"], paper["blocks"])

    def test_context_budget_preserves_current_section_and_json(self):
        paper = self.paper()
        paper["blocks"][0]["zh"] = "摘要😀" * 2000
        paper["blocks"][1:1] = [
            {"id": f"h{i}", "page": 1, "type": "heading", "zh": f"章节{i}" + "长标题" * 100}
            for i in range(100)
        ]
        text, data = self.focus(paper, [3])
        self.assertLessEqual(len(text), 2400)
        self.assertEqual(data["current_sections"], [{"page": 2, "title": "3 方法"}])
        self.assertEqual(text, self.focus(paper, [3])[0])

    def test_no_abstract_does_not_promote_arbitrary_body_to_summary(self):
        paper = self.paper()
        paper["blocks"] = paper["blocks"][1:]
        _, data = self.focus(paper, [3])
        self.assertEqual(data["abstract"], "")
        self.assertNotIn("本批固定译文", json.dumps(data, ensure_ascii=False))

    def test_escaped_abstract_and_section_titles_cannot_overflow_empty_outline(self):
        paper = self.paper()
        paper["blocks"][0]["zh"] = '"' * 1000
        paper["blocks"].extend(
            {"id": f"escaped{i}", "page": 3, "type": "heading", "zh": str(i) + '"' * 119}
            for i in range(6)
        )
        text, data = self.focus(paper, [3])
        self.assertLessEqual(len(text), 2400)
        self.assertEqual(data["current_sections"][0], {"page": 2, "title": "3 方法"})
        self.assertTrue(data["abstract"])


if __name__ == "__main__":
    unittest.main()

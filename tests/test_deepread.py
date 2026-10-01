import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyread import deepread
from easyread.jobs import Jobs
from easyread.store import Workspace, write_json_atomic


class DeepReadCoreTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        write_json_atomic(self.root / "paper.json", {
            "meta": {"title_zh": "测试论文", "title_en": "Test Paper"},
            "blocks": [
                {"id": "h1", "type": "heading", "zh": "中文标题", "en": "English heading"},
                {"id": "p1", "type": "para", "zh": "中文段落", "en": "English paragraph should not win"},
                {"id": "p2", "type": "para", "zh": "", "en": "Fallback English paragraph"},
                {"id": "f1", "type": "figure", "caption_zh": "中文图注", "caption_en": "English caption"},
            ],
        })
        self.ws = Workspace(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_context_prefers_translated_text_and_falls_back_to_english(self):
        text = deepread.context(self.ws)
        self.assertIn("中文段落", text)
        self.assertNotIn("English paragraph should not win", text)
        self.assertIn("Fallback English paragraph", text)
        self.assertIn("中文图注", text)

    def test_context_includes_untranslated_page_extract(self):
        extract = self.root / "extract"
        extract.mkdir()
        (extract / "page-002.txt").write_text("An untranslated page from the original paper.", encoding="utf-8")
        text = deepread.context(self.ws)
        self.assertIn("An untranslated page from the original paper.", text)

    def test_limit_never_exceeds_5000_characters(self):
        text = deepread.limit("x" * 6000)
        self.assertEqual(len(text), 5000)
        self.assertLessEqual(len(text), deepread.MAX_CHARS)

    def test_build_prompt_contains_custom_prompt_and_fixed_constraints(self):
        prompt = deepread.build_prompt(self.ws, "重点讲清楚方法和局限")
        self.assertIn("重点讲清楚方法和局限", prompt)
        self.assertIn("5000", prompt)
        self.assertIn("不要编造", prompt)
        self.assertIn("中文段落", prompt)

    def test_generate_writes_bounded_document_and_success_state(self):
        cfg = {
            "claude": {}, "codex": {}, "openai": {},
            "chat": {"default": "custom", "models": [{
                "id": "custom", "engine": "openai", "model": "relay-model",
                "base_url": "https://relay.example/v1", "api_key": "secret",
            }]},
            "deepread": {"model": "custom", "prompt": "讲清楚方法"},
        }
        with mock.patch.object(deepread.engines, "run", return_value="y" * 6000) as run:
            result = deepread.generate(self.ws, cfg)
        self.assertEqual(len(result), 5000)
        self.assertEqual(len((self.root / "deepread.md").read_text(encoding="utf-8")), 5000)
        state = json.loads((self.root / "deepread.json").read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "done")
        self.assertEqual(state["chars"], 5000)
        run.assert_called_once()

    def test_generate_failure_keeps_previous_document(self):
        (self.root / "deepread.md").write_text("旧结果", encoding="utf-8")
        cfg = {"claude": {}, "codex": {}, "openai": {}, "chat": {"default": "custom", "models": [{
            "id": "custom", "engine": "openai", "model": "relay-model", "base_url": "x", "api_key": "k",
        }]}, "deepread": {"model": "custom", "prompt": ""}}
        with mock.patch.object(deepread.engines, "run", side_effect=RuntimeError("接口失败")):
            with self.assertRaises(RuntimeError):
                deepread.generate(self.ws, cfg)
        self.assertEqual((self.root / "deepread.md").read_text(encoding="utf-8"), "旧结果")

    def test_submit_deepread_does_not_duplicate_active_job(self):
        jobs = Jobs.__new__(Jobs)
        jobs.lock = __import__("threading").Lock()
        jobs.recent = []
        jobs.small = __import__("queue").Queue()
        jobs.lib = mock.Mock()
        jobs.submit_small = mock.Mock(return_value={"state": "queued"})
        cfg = {"deepread": {"model": "m", "prompt": "p"}}
        with mock.patch.object(deepread, "claim", return_value=({"state": "queued"}, True)):
            first = jobs.submit_deepread(self.ws, cfg)
        self.assertEqual(first["state"], "queued")
        jobs.submit_small.assert_called_once_with("deepread", self.ws.id)

        jobs.submit_small.reset_mock()
        with mock.patch.object(deepread, "claim", return_value=({"state": "running"}, False)):
            second = jobs.submit_deepread(self.ws, cfg)
        self.assertEqual(second["state"], "running")
        jobs.submit_small.assert_not_called()

    def test_claim_makes_only_the_first_request_active(self):
        first, created = deepread.claim(self.ws, "model-a", "prompt-a")
        self.assertTrue(created)
        self.assertEqual(first["state"], "queued")
        second, created = deepread.claim(self.ws, "model-b", "prompt-b")
        self.assertFalse(created)
        self.assertEqual(second["state"], "queued")
        self.assertEqual(second["model"], "model-a")


if __name__ == "__main__":
    unittest.main()

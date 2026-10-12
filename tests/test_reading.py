"""六色语义高亮：定位、校验、随翻译批次落库。  python -m unittest tests.test_reading"""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from easyread import engines, reading, translate
from easyread.paperdata import merge_blocks, set_block_text
from easyread.store import Workspace, write_json_atomic


def make_ws(n_pages: int = 2) -> Workspace:
    root = Path(tempfile.mkdtemp(prefix="easyread-reading-"))
    (root / "extract").mkdir()
    for n in range(1, n_pages + 1):
        (root / "extract" / f"page-{n:03d}.txt").write_text(f"Text of page {n}.", encoding="utf-8")
    pages = [{"n": n, "w": 600, "h": 800, "img": f"pages/page-{n:03d}.webp"} for n in range(1, n_pages + 1)]
    write_json_atomic(root / "paper.json", {"meta": {"pages": pages, "page_count": n_pages}, "translation": {"done_pages": []},
                                            "glossary": [], "references": [], "blocks": []})
    return Workspace(root)


class LocateQuoteTest(unittest.TestCase):
    def test_chinese_and_occurrences(self):
        text = "这种方法很好。这种方法很快。这种方法很稳。"
        self.assertEqual(reading.locate_quote(text, "这种方法", 0), (0, 4))
        self.assertEqual(reading.locate_quote(text, "这种方法", 1), (7, 11))
        self.assertEqual(reading.locate_quote(text, "这种方法", 2), (14, 18))
        self.assertIsNone(reading.locate_quote(text, "这种方法", 3))

    def test_english_mixed_and_missing(self):
        text = "Transformer 使用 self-attention 机制。"
        self.assertEqual(reading.locate_quote(text, "self-attention", 0), (15, 29))
        self.assertEqual(reading.locate_quote(text, "机制", 0), (30, 32))
        self.assertIsNone(reading.locate_quote(text, "不存在的短语", 0))
        self.assertIsNone(reading.locate_quote(text, "", 0))
        self.assertIsNone(reading.locate_quote(text, "Transformer", -1))


class ValidateHighlightsTest(unittest.TestCase):
    def para(self, **kw):
        b = {"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出一种新的注意力机制，并做了大量实验。", **kw}
        return [b]

    def test_valid_highlight_kept_with_deterministic_id(self):
        blocks = self.para()
        blocks[0]["highlights"] = [{"category": "method", "quote": "新的注意力机制"}]
        problems = reading.validate_highlights(blocks)
        self.assertEqual(problems, [])
        self.assertEqual(blocks[0]["highlights"], [
            {"id": "h-p1-1-0", "category": "method", "quote": "新的注意力机制", "occurrence": 0}])

    def test_invalid_category_dropped(self):
        blocks = self.para(highlights=[{"category": "important", "quote": "新的注意力机制"}])
        problems = reading.validate_highlights(blocks)
        self.assertEqual(len(problems), 1)
        self.assertNotIn("highlights", blocks[0])

    def test_quote_not_in_text_dropped(self):
        blocks = self.para(highlights=[{"category": "method", "quote": "不存在的句子"}])
        self.assertEqual(len(reading.validate_highlights(blocks)), 1)
        self.assertNotIn("highlights", blocks[0])

    def test_occurrence_out_of_range_dropped(self):
        blocks = self.para(highlights=[{"category": "method", "quote": "新的注意力机制", "occurrence": 5}])
        self.assertEqual(len(reading.validate_highlights(blocks)), 1)

    def test_overlapping_and_duplicate_dropped(self):
        hl = [{"category": "method", "quote": "新的注意力机制", "occurrence": 0},
              {"category": "innovation", "quote": "注意力机制，", "occurrence": 0},
              {"category": "evidence", "quote": "新的注意力机制", "occurrence": 0}]
        blocks = self.para(highlights=hl)
        problems = reading.validate_highlights(blocks)
        self.assertEqual(len(problems), 2)
        self.assertEqual([h["category"] for h in blocks[0]["highlights"]], ["method"])

    def test_list_and_caption_units(self):
        blocks = [{"id": "l1", "type": "list", "page": 1, "ordered": True,
                   "items": [{"en": "a", "zh": "第一条"}, {"en": "b", "zh": "第二条"}],
                   "highlights": [{"category": "method", "quote": "第二条"}]},
                  {"id": "fig1", "type": "figure", "page": 1, "caption_zh": "图 1：整体架构",
                   "highlights": [{"category": "method", "quote": "整体架构"}]},
                  {"id": "eq1", "type": "math", "page": 1, "tex": "x=y",
                   "highlights": [{"category": "method", "quote": "x"}]}]
        reading.validate_highlights(blocks)
        self.assertEqual(blocks[0]["highlights"][0]["quote"], "第二条")
        self.assertEqual(blocks[1]["highlights"][0]["quote"], "整体架构")
        self.assertNotIn("highlights", blocks[2])

    def test_model_extra_fields_stripped(self):
        blocks = self.para(highlights=[{"id": "evil", "category": "method", "quote": "新的注意力机制",
                                        "occurrence": 0, "color": "#FF0000", "start": 3}])
        reading.validate_highlights(blocks)
        self.assertEqual(blocks[0]["highlights"][0]["id"], "h-p1-1-0")
        self.assertNotIn("color", blocks[0]["highlights"][0])
        self.assertNotIn("start", blocks[0]["highlights"][0])

    def test_not_a_list_is_problem(self):
        blocks = self.para(highlights="not a list")
        self.assertEqual(len(reading.validate_highlights(blocks)), 1)
        self.assertNotIn("highlights", blocks[0])


class ReadingStateTest(unittest.TestCase):
    def test_merge_blocks_writes_reading_state(self):
        ws = make_ws()
        data = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "译文",
                            "highlights": [{"id": "h-p1-1-0", "category": "method", "quote": "译文", "occurrence": 0}]}]}
        merge_blocks(ws, data, done=[1], replace_pages=[1], reading={"pages": [1], "status": "completed", "enabled": True})
        paper = ws.load("paper")
        self.assertEqual(paper["reading"]["version"], reading.READING_VERSION)
        self.assertTrue(paper["reading"]["enabled"])
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")
        self.assertTrue(paper["reading"]["pages"]["1"]["zh_hash"])
        self.assertEqual(paper["blocks"][0]["highlights"][0]["category"], "method")
        shutil.rmtree(ws.root, ignore_errors=True)

    def test_skipped_status_when_disabled(self):
        ws = make_ws()
        merge_blocks(ws, {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "zh": "译文"}]},
                    reading={"pages": [1], "status": "skipped", "enabled": False})
        self.assertEqual(ws.load("paper")["reading"]["pages"]["1"]["status"], "skipped")
        self.assertFalse(ws.load("paper")["reading"]["enabled"])
        shutil.rmtree(ws.root, ignore_errors=True)

    def test_retranslated_block_drops_highlights(self):
        ws = make_ws()
        merge_blocks(ws, {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "zh": "旧译文",
                                      "highlights": [{"id": "h", "category": "method", "quote": "旧译文", "occurrence": 0}]}]},
                    reading={"pages": [1], "status": "completed", "enabled": True})
        set_block_text(ws, "p1-1", "全新的译文")
        b = ws.load("paper")["blocks"][0]
        self.assertEqual(b["zh"], "全新的译文")
        self.assertNotIn("highlights", b)
        shutil.rmtree(ws.root, ignore_errors=True)

    def test_retranslated_block_marks_page_stale(self):
        ws = make_ws()
        merge_blocks(ws, {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "zh": "旧译文",
                                      "highlights": [{"id": "h", "category": "method", "quote": "旧译文", "occurrence": 0}]}]},
                    reading={"pages": [1], "status": "completed", "enabled": True})
        set_block_text(ws, "p1-1", "全新的译文")
        self.assertEqual(ws.load("paper")["reading"]["pages"]["1"]["status"], "stale")
        shutil.rmtree(ws.root, ignore_errors=True)

    def test_old_paper_without_reading_fields_loads(self):
        ws = make_ws()
        self.assertNotIn("reading", ws.load("paper"))
        shutil.rmtree(ws.root, ignore_errors=True)


class TranslateIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.ws = make_ws(2)
        self.cfg = {"engine": "openai", "batch_pages": 2, "concurrency": 1, "openai": {"vision": False}}
        self.patches = [mock.patch.object(translate.pdfwork, "locate", lambda root: None),
                        mock.patch.object(translate, "tex_problems", lambda tex: [])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.ws.root, ignore_errors=True)

    def _run(self, result_json, reading_enabled=True):
        prompts_seen = []

        def run(cfg, prompt, cwd, images=None, cancel=None):
            prompts_seen.append(prompt)
            return result_json if isinstance(result_json, str) else json.dumps(result_json)

        cfg = dict(self.cfg, reading_enabled=reading_enabled)
        with mock.patch.object(engines, "run", run):
            failed = translate.translate_pages(self.ws, cfg, [1, 2], threading.Event(), lambda *a: None)
        return failed, prompts_seen

    def test_highlights_saved_with_translation(self):
        ok = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。",
                          "highlights": [{"category": "method", "quote": "新方法", "occurrence": 0}]},
                         {"id": "p2-1", "type": "para", "page": 2, "en": "y", "zh": "实验证明有效。"}]}
        failed, _ = self._run(ok)
        self.assertEqual(failed, {})
        paper = self.ws.load("paper")
        self.assertEqual(paper["blocks"][0]["highlights"][0]["quote"], "新方法")
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")
        self.assertEqual(paper["reading"]["pages"]["2"]["status"], "completed")

    def test_bad_highlights_dropped_translation_kept(self):
        bad = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。",
                           "highlights": [{"category": "huh", "quote": "新方法"},
                                          {"category": "method", "quote": "定位不到的引文"}]}]}
        failed, _ = self._run(bad)
        self.assertEqual(failed, {})
        paper = self.ws.load("paper")
        self.assertNotIn("highlights", paper["blocks"][0])
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "failed")  # 精读坏了，但译文保住

    def test_reading_off_means_pure_translation(self):
        ok = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。"}]}
        failed, prompts_seen = self._run(ok, reading_enabled=False)
        self.assertEqual(failed, {})
        self.assertNotIn("语义高亮", prompts_seen[0])
        self.assertEqual(self.ws.load("paper")["reading"]["pages"]["1"]["status"], "skipped")

    def test_reading_on_adds_prompt_rules(self):
        ok = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。"}]}
        _, prompts_seen = self._run(ok, reading_enabled=True)
        self.assertIn("语义高亮", prompts_seen[0])

    def test_annotations_saved_with_translation(self):
        ok = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。",
                          "highlights": [{"category": "method", "quote": "新方法", "occurrence": 0}],
                          "annotations": [{"type": "concept", "anchor_quote": "新方法", "content": "这里的方法指……",
                                           "basis": "author_statement"}]}]}
        failed, _ = self._run(ok)
        self.assertEqual(failed, {})
        paper = self.ws.load("paper")
        ann = paper["blocks"][0]["annotations"][0]
        self.assertEqual(ann["type"], "concept")
        self.assertEqual(ann["basis"], "author_statement")
        self.assertEqual(ann["id"], "a-p1-1-0")
        self.assertEqual(paper["reading"]["pages"]["1"]["annotations"], 1)

    def test_bad_annotations_dropped_page_failed(self):
        bad = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。",
                           "annotations": [{"type": "nope", "content": "x"},
                                          {"type": "concept", "anchor_quote": "找不到的锚点", "content": "y"}]}]}
        failed, _ = self._run(bad)
        self.assertEqual(failed, {})
        paper = self.ws.load("paper")
        self.assertNotIn("annotations", paper["blocks"][0])
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "failed")


class EnrichPagesTest(unittest.TestCase):
    def setUp(self):
        self.ws = make_ws(2)
        self.cfg = {"engine": "openai", "batch_pages": 2, "concurrency": 1, "openai": {"vision": False},
                    "reading_enabled": True}
        merge_blocks(self.ws, {"blocks": [
            {"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。"},
            {"id": "p2-1", "type": "para", "page": 2, "en": "y", "zh": "实验证明有效。"}]}, done=[1, 2])
        self.patches = [mock.patch.object(translate.pdfwork, "locate", lambda root: None),
                        mock.patch.object(translate, "tex_problems", lambda tex: [])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.ws.root, ignore_errors=True)

    def test_pages_needing_reading_picks_missing_and_failed(self):
        from easyread import reading
        paper = self.ws.load("paper")
        self.assertEqual(reading.pages_needing_reading(paper), [1, 2])  # 老论文没有 reading 字段：全缺
        self.ws.update("paper", lambda p: (reading.apply_reading_state(p, [1], "completed", True),
                                           reading.apply_reading_state(p, [2], "failed", True)))
        self.assertEqual(reading.pages_needing_reading(self.ws.load("paper")), [2])

    def test_enrich_writes_annotations_without_touching_translation(self):
        reply = {"blocks": [{"id": "p1-1", "highlights": [{"category": "method", "quote": "新方法", "occurrence": 0}],
                             "annotations": [{"type": "concept", "anchor_quote": "新方法", "content": "指文中的机制。",
                                              "basis": "source_supported"}]}]}
        with mock.patch.object(engines, "run", mock.Mock(return_value=json.dumps(reply))):
            failed = translate.enrich_pages(self.ws, self.cfg, None, threading.Event(), lambda *a: None)
        self.assertEqual(failed, {})
        paper = self.ws.load("paper")
        b = paper["blocks"][0]
        self.assertEqual(b["zh"], "我们提出新方法。")  # 译文没动
        self.assertEqual(b["highlights"][0]["category"], "method")
        self.assertEqual(b["annotations"][0]["type"], "concept")
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")

    def test_enrich_skips_when_reading_disabled(self):
        with mock.patch.object(engines, "run", mock.Mock()) as run:
            failed = translate.enrich_pages(self.ws, dict(self.cfg, reading_enabled=False), None, None, lambda *a: None)
        self.assertEqual(failed, {})
        run.assert_not_called()

    def test_enrich_failure_records_pages(self):
        with mock.patch.object(engines, "run", mock.Mock(side_effect=engines.EngineError("boom"))):
            failed = translate.enrich_pages(self.ws, self.cfg, [1], threading.Event(), lambda *a: None)
        self.assertEqual(list(failed), [1])
        self.assertIn("补精读第 [1] 页失败", (self.ws.root / "job.log").read_text(encoding="utf-8"))

    def test_enrich_ignores_unknown_blocks(self):
        reply = {"blocks": [{"id": "nope", "highlights": [{"category": "method", "quote": "x", "occurrence": 0}]}]}
        with mock.patch.object(engines, "run", mock.Mock(return_value=json.dumps(reply))):
            failed = translate.enrich_pages(self.ws, self.cfg, [1], threading.Event(), lambda *a: None)
        self.assertEqual(failed, {})
        self.assertNotIn("highlights", self.ws.load("paper")["blocks"][0])


if __name__ == "__main__":
    unittest.main()


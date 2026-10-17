"""六色语义高亮：定位、校验、翻译后手动精读。  python -m unittest tests.test_reading"""
import copy
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
            {"id": "h-p1-1-0", "block_id": "p1-1", "category": "method", "quote": "新的注意力机制", "occurrence": 0}])

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
        self.assertEqual(len(problems), 3)
        self.assertNotIn("highlights", blocks[0])

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
        # A two-page successful fixture must actually contain both pages.
        if isinstance(result_json, dict) and not any(b.get("page") == 2 for b in result_json.get("blocks", [])):
            result_json["blocks"].append({"id": "p2-1", "type": "para", "page": 2,
                                          "en": "Text of page 2.", "zh": "第二页译文。"})

        def run(cfg, prompt, cwd, images=None, cancel=None):
            prompts_seen.append(prompt)
            return result_json if isinstance(result_json, str) else json.dumps(result_json)

        cfg = dict(self.cfg, reading_enabled=reading_enabled)
        with mock.patch.object(engines, "run", run):
            failed = translate.translate_pages(self.ws, cfg, [1, 2], threading.Event(), lambda *a: None)
        return failed, prompts_seen

    def _enrich(self, reply, pages=(1, 2)):
        with mock.patch.object(engines, "run", return_value=json.dumps(reply)) as run:
            failed = translate.enrich_pages(self.ws, self.cfg, list(pages), threading.Event(), lambda *a: None)
        return failed, run

    def assert_translation_only(self, status="pending"):
        paper = self.ws.load("paper")
        self.assertEqual(paper["translation"]["done_pages"], [1, 2])
        for block in paper["blocks"]:
            self.assertNotIn("highlights", block)
            self.assertNotIn("annotations", block)
        self.assertEqual(paper["reading"].get("insights", []), [])
        for page in ("1", "2"):
            self.assertEqual(paper["reading"]["pages"][page]["status"], status)
        self.assertEqual(paper["reading_guide"]["coverage"]["analyzed"], [])
        self.assertFalse(paper["reading_guide"].get("synth_of"))

    def test_highlights_saved_only_after_manual_enrich(self):
        ok = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。",
                          "highlights": [{"category": "method", "quote": "新方法", "occurrence": 0}]},
                         {"id": "p2-1", "type": "para", "page": 2, "en": "y", "zh": "实验证明有效。"}]}
        failed, calls = self._run(ok)
        self.assertEqual(failed, {})
        self.assertEqual(len(calls), 1)
        self.assert_translation_only()
        failed, run = self._enrich(ok)
        self.assertEqual(failed, {})
        self.assertEqual(run.call_count, 1)
        paper = self.ws.load("paper")
        self.assertEqual([b["zh"] for b in paper["blocks"]], ["我们提出新方法。", "实验证明有效。"])
        self.assertEqual(paper["blocks"][0]["highlights"][0]["quote"], "新方法")
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")
        self.assertEqual(paper["reading"]["pages"]["2"]["status"], "completed")

    def test_bad_highlights_dropped_translation_kept(self):
        bad = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。",
                           "highlights": [{"category": "huh", "quote": "新方法"},
                                          {"category": "method", "quote": "定位不到的引文"}]}]}
        failed, _ = self._run(bad)
        self.assertEqual(failed, {})
        self.assert_translation_only()
        # A valid sibling survives even when two other highlights are invalid.
        bad["blocks"][0]["highlights"].append({"category": "method", "quote": "我们"})
        failed, run = self._enrich(bad)
        self.assertEqual(failed, {})
        self.assertEqual(run.call_count, 1)  # highlight problems do not start a repair
        paper = self.ws.load("paper")
        self.assertEqual([h["quote"] for h in paper["blocks"][0]["highlights"]], ["我们"])
        self.assertEqual(paper["blocks"][0]["zh"], "我们提出新方法。")
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")
        self.assertEqual(paper["reading"]["pages"]["2"]["status"], "completed")
        self.assertEqual(reading.pages_needing_reading(paper), [])
        self.assertIn("高亮错误=2，批注错误=0，研究要点错误=0",
                      (self.ws.root / "job.log").read_text(encoding="utf-8"))

    def test_reading_off_means_pure_translation(self):
        ok = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。"}]}
        failed, prompts_seen = self._run(ok, reading_enabled=False)
        self.assertEqual(failed, {})
        self.assertNotIn("语义高亮", prompts_seen[0])
        self.assertEqual(len(prompts_seen), 1)
        self.assert_translation_only("skipped")
        self.assertFalse(self.ws.load("paper")["reading"]["enabled"])

    def test_reading_on_still_uses_translation_only_prompt(self):
        ok = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。"}]}
        failed, prompts_seen = self._run(ok, reading_enabled=True)
        self.assertEqual(failed, {})
        self.assertEqual(len(prompts_seen), 1)
        self.assertNotIn("语义高亮", prompts_seen[0])
        self.assertNotIn('"highlights"', prompts_seen[0])
        self.assertNotIn('"annotations"', prompts_seen[0])
        self.assertNotIn('"insights"', prompts_seen[0])
        self.assert_translation_only()

    def test_annotations_saved_only_after_manual_enrich(self):
        ok = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。",
                          "highlights": [{"category": "method", "quote": "新方法", "occurrence": 0}],
                          "annotations": [{"type": "concept", "anchor_quote": "新方法", "content": "这里的方法指……",
                                           "basis": "author_statement"}]}]}
        failed, _ = self._run(ok)
        self.assertEqual(failed, {})
        self.assert_translation_only()
        failed, run = self._enrich(ok)
        self.assertEqual(failed, {})
        self.assertEqual(run.call_count, 1)
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
        self.assert_translation_only()
        failed, run = self._enrich(bad)
        self.assertEqual(set(failed), {1})
        self.assertEqual(run.call_count, 2)  # invalid annotations still get one strict repair
        paper = self.ws.load("paper")
        self.assertNotIn("annotations", paper["blocks"][0])
        self.assertEqual(paper["blocks"][0]["zh"], "我们提出新方法。")
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "failed")
        self.assertEqual(paper["reading"]["pages"]["2"]["status"], "completed")


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
        original = copy.deepcopy(self.ws.load("paper")["blocks"])
        reply = {"blocks": [{"id": "p1-1", "highlights": [{"category": "method", "quote": "新方法", "occurrence": 0}],
                             "en": "invented source", "zh": "不允许覆盖译文",
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
        self.assertEqual(paper["reading"]["pages"]["2"]["status"], "completed")
        self.assertEqual([{k: v for k, v in b.items() if k not in ("highlights", "annotations")}
                          for b in paper["blocks"]], original)

    def test_manual_enrich_runs_when_reading_disabled(self):
        reply = {"reviewed_pages": [1, 2], "blocks": [
            {"id": "p1-1", "highlights": [{"category": "method", "quote": "新方法"}]}]}
        with mock.patch.object(engines, "run", return_value=json.dumps(reply)) as run:
            failed = translate.enrich_pages(self.ws, dict(self.cfg, reading_enabled=False), None, None, lambda *a: None)
        self.assertEqual(failed, {})
        self.assertEqual(run.call_count, 1)
        paper = self.ws.load("paper")
        self.assertTrue(paper["reading"]["enabled"])
        self.assertEqual(paper["blocks"][0]["highlights"][0]["quote"], "新方法")
        self.assertEqual([paper["reading"]["pages"][str(p)]["status"] for p in (1, 2)],
                         ["completed", "completed"])

    def test_enrich_none_reruns_all_translated_pages_including_completed(self):
        self.ws.update("paper", lambda p: (reading.apply_reading_state(p, [1], "completed", True),
                                           reading.apply_reading_state(p, [2], "failed", True)))
        self.assertEqual(reading.pages_needing_reading(self.ws.load("paper")), [2])
        with mock.patch.object(engines, "run", return_value='{"blocks": [], "reviewed_pages": [1, 2]}') as run:
            failed = translate.enrich_pages(self.ws, self.cfg, None, None, lambda *a: None)
        self.assertEqual(failed, {})
        self.assertEqual(run.call_count, 1)
        metrics = [json.loads(line) for line in (self.ws.root / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(entry["kind"], entry["pages"]) for entry in metrics], [("enrich", [1, 2])])
        self.assertEqual(reading.pages_needing_reading(self.ws.load("paper")), [])

    def test_explicit_untranslated_pages_do_not_start_enrich(self):
        with mock.patch.object(engines, "run") as run:
            self.assertEqual(translate.enrich_pages(self.ws, self.cfg, [3], None, lambda *a: None), {})
        run.assert_not_called()

    def test_enrich_failure_records_pages(self):
        with mock.patch.object(engines, "run", mock.Mock(side_effect=engines.EngineError("boom"))):
            failed = translate.enrich_pages(self.ws, self.cfg, [1], threading.Event(), lambda *a: None)
        self.assertEqual(list(failed), [1])
        self.assertIn("精读第 [1] 页失败", (self.ws.root / "job.log").read_text(encoding="utf-8"))

    def test_enrich_ignores_unknown_blocks(self):
        reply = {"blocks": [{"id": "nope", "highlights": [{"category": "method", "quote": "x", "occurrence": 0}]}]}
        with mock.patch.object(engines, "run", mock.Mock(return_value=json.dumps(reply))):
            failed = translate.enrich_pages(self.ws, self.cfg, [1], threading.Event(), lambda *a: None)
        self.assertEqual(list(failed), [1])
        self.assertNotIn("highlights", self.ws.load("paper")["blocks"][0])


class InsightsTest(unittest.TestCase):
    def blocks(self):
        return [{"id": "p1-1", "type": "para", "page": 1, "zh": "我们提出新方法。"},
                {"id": "p1-2", "type": "para", "page": 1, "zh": "实验证明有效。"}]

    def test_validate_insights_keeps_valid(self):
        raw = [{"type": "problem", "content": "如何让模型更小？", "block": "p1-1", "status": "provisional"},
               {"type": "evidence", "content": "准确率提升 2%。", "block": "p1-2", "status": "supported"}]
        out, problems = reading.validate_insights(raw, self.blocks())
        self.assertEqual(problems, [])
        self.assertEqual([i["type"] for i in out], ["problem", "evidence"])
        self.assertEqual(out[0]["page"], 1)

    def test_validate_insights_drops_bad(self):
        raw = [{"type": "huh", "content": "x", "block": "p1-1"},
               {"type": "method", "content": "", "block": "p1-1"},
               {"type": "method", "content": "ok", "block": "nope"},
               {"type": "method", "content": "ok", "block": "p1-1", "status": "maybe"},
               {"type": "method", "content": "好的", "block": "p1-1"}]
        out, problems = reading.validate_insights(raw, self.blocks())
        self.assertEqual(len(out), 1)
        self.assertEqual(len(problems), 4)
        self.assertEqual(out[0]["status"], "provisional")  # 缺省补默认

    def test_merge_insights_dedups_same_content(self):
        a = [{"type": "problem", "content": "如何让模型更小？", "block": "p1-1", "page": 1, "status": "provisional"}]
        b = [{"type": "problem", "content": "如何让模型更小？", "block": "p2-1", "page": 2, "status": "supported"},
             {"type": "method", "content": "提出剪枝方法。", "block": "p1-2", "page": 1, "status": "provisional"}]
        out = reading.merge_insights(a, b)
        self.assertEqual(len(out), 2)
        hit = out[0]
        self.assertEqual(hit["status"], "provisional")  # 没有直接引文，不能升级成已支持
        self.assertEqual(len(hit["source_refs"]), 2)  # 两处出处都留
        self.assertTrue(hit["id"])

    def test_drop_insights_on_retranslated_pages(self):
        insights = [
            {"id": "i1", "type": "problem", "content": "a", "page": 3, "block": "p3-1",
             "source_refs": [{"page": 3, "block": "p3-1"}]},
            {"id": "i2", "type": "method", "content": "b", "page": 3, "block": "p3-2",
             "source_refs": [{"page": 3, "block": "p3-2"}, {"page": 5, "block": "p5-1"}]},
        ]
        out = reading.drop_insights_on_pages(insights, [3])
        self.assertEqual([i["id"] for i in out], ["i2"])  # 全在重译页的丢，跨页的留


class GuideTest(unittest.TestCase):
    def paper(self, insights, done=(1, 2), analyzed=(1, 2)):
        reading_state = {"insights": insights,
                         "pages": {str(p): {"status": "completed"} for p in analyzed}}
        paper = {"meta": {"page_count": 2}, "translation": {"done_pages": list(done)},
                 "reading": reading_state, "blocks": [{"id": f"p{p}-1", "page": p, "type": "para", "en": "x", "zh": "译文"} for p in (1, 2)]}
        reading.apply_reading_state(paper, list(analyzed), "completed", True)
        return paper

    def test_rebuild_guide_groups_and_coverage(self):
        p = self.paper([
            {"id": "i1", "type": "problem", "content": "a", "page": 1, "block": "p1-1"},
            {"id": "i2", "type": "method", "content": "b", "page": 1, "block": "p1-1"},
            {"id": "i3", "type": "problem", "content": "c", "page": 2, "block": "p2-1"},
        ])
        reading.rebuild_guide(p)
        g = p["reading_guide"]
        self.assertEqual(g["problems"], ["i1", "i3"])
        self.assertEqual(g["methods"], ["i2"])
        self.assertEqual(g["coverage"]["analyzed"], [1, 2])
        self.assertEqual(g["status"], "complete")

    def test_rebuild_guide_partial_when_analysis_incomplete(self):
        p = self.paper([{"id": "i1", "type": "problem", "content": "a", "page": 1, "block": "p1-1"}],
                       done=(1, 2), analyzed=(1,))
        reading.rebuild_guide(p)
        self.assertEqual(p["reading_guide"]["status"], "partial")
        self.assertEqual(p["reading_guide"]["coverage"]["analyzed"], [1])

    def test_apply_synthesis_validates_ids_and_statuses(self):
        p = self.paper([{"id": "i1", "type": "innovation", "content": "a", "page": 1, "block": "p1-1", "status": "provisional"}])
        relations = [{"from": "i1", "to": "i2", "content": "x"},  # i2 不存在，丢弃
                     {"from": "i1", "to": "i1", "content": "方法支撑问题"}]
        refs = [{"page": 1, "block": "p1-1", "source_quote": "x"}]
        reading.apply_synthesis(p, "一句话总结。", relations, [{"insight": "i1", "status": "supported", "source_refs": refs}], summary_refs=refs)
        g = p["reading_guide"]
        self.assertEqual(g["summary"], "一句话总结。")
        self.assertEqual(len(g["relations"]), 0)  # 无来源关系和自连关系均不展示
        self.assertEqual(p["reading"]["insights"][0]["status"], "supported")
        self.assertTrue(g["synth_of"])


class GuideIntegrationTest(unittest.TestCase):
    """翻译 → 手动精读及 insights 落库 → 已分析范围完成后一次综合。"""

    def setUp(self):
        self.ws = make_ws(2)
        self.cfg = {"engine": "openai", "batch_pages": 2, "concurrency": 1, "openai": {"vision": False},
                    "reading_enabled": True}
        for p in [mock.patch.object(translate.pdfwork, "locate", lambda root: None),
                  mock.patch.object(translate, "tex_problems", lambda tex: [])]:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.ws.root, ignore_errors=True)

    def test_translate_then_manual_enrich_builds_guide(self):
        reply = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。",
                            "highlights": [{"category": "method", "quote": "新方法", "occurrence": 0}]},
                           {"id": "p2-1", "type": "para", "page": 2, "en": "y", "zh": "实验证明有效。"}],
                   "insights": [{"type": "problem", "content": "如何提升效率？", "block": "p1-1", "status": "provisional"},
                                {"type": "method", "content": "提出新方法。", "block": "p1-1", "status": "provisional"},
                                {"type": "evidence", "content": "实验证明有效。", "block": "p2-1", "status": "supported"}]}
        synth = {"summary": "本文提出新方法提升效率。", "summary_refs": [{"page": 1, "block": "p1-1", "source_quote": "Text of page 1."}],
                 "relations": [{"from": "i1-xxxxxx", "to": "i2-yyyyyy", "content": "问题由方法解决"}],
                 "checks": []}
        calls = []

        def run(cfg, prompt, cwd, images=None, cancel=None):
            calls.append(prompt)
            return json.dumps(synth if "请整合成研究导航" in prompt else reply)

        with mock.patch.object(engines, "run", run):
            failed = translate.translate_pages(self.ws, self.cfg, [1, 2], threading.Event(), lambda *a: None)
            self.assertEqual(failed, {})
            translated = self.ws.load("paper")
            self.assertEqual(len(calls), 1)
            self.assertEqual(translated["reading"].get("insights", []), [])
            self.assertEqual(translated["reading_guide"]["coverage"]["analyzed"], [])
            self.assertFalse(translated["reading_guide"].get("synth_of"))
            for block in translated["blocks"]:
                self.assertNotIn("highlights", block)
                self.assertNotIn("annotations", block)
            self.assertFalse(translate.finalize_reading(self.ws, self.cfg, None))
            self.assertEqual(len(calls), 1)
            self.assertEqual(translate.enrich_pages(self.ws, self.cfg, None, threading.Event(), lambda *a: None), {})

        self.assertEqual(len(calls), 3)  # translate, manual enrich, synthesis
        self.assertEqual(len([c for c in calls if "请整合成研究导航" in c]), 1)
        paper = self.ws.load("paper")
        self.assertEqual(len(paper["reading"]["insights"]), 3)
        guide = paper["reading_guide"]
        self.assertEqual(guide["status"], "complete")
        self.assertEqual(guide["summary"], "本文提出新方法提升效率。")
        self.assertEqual(len(guide["relations"]), 0)  # 关系引用的 id 不存在 → 程序丢弃
        # 再收尾一次：insights 没变，不再调模型
        with mock.patch.object(engines, "run", mock.Mock()) as run2:
            self.assertFalse(translate.finalize_reading(self.ws, self.cfg, None))
            run2.assert_not_called()

    def test_finalize_synthesizes_partial_scope(self):
        reply = {"blocks": [{"id": "p1-1", "type": "para", "page": 1, "en": "x", "zh": "我们提出新方法。"}],
                 "insights": [{"type": "problem", "content": "如何提升效率？", "block": "p1-1", "status": "provisional"}]}
        synth = {"summary": "范围总结。", "summary_refs": [{"page": 1, "block": "p1-1", "source_quote": "Text of page 1."}], "relations": [], "checks": []}
        with mock.patch.object(engines, "run", mock.Mock(return_value=json.dumps(reply))):
            self.assertEqual(translate.translate_pages(self.ws, dict(self.cfg, _target_pages=[1], _scope="first:1"),
                                                      [1], threading.Event(), lambda *a: None), {})
        before = self.ws.load("paper")
        self.assertEqual(before["reading"]["pages"]["1"]["status"], "pending")
        self.assertEqual(before["reading"].get("insights", []), [])
        self.assertEqual(before["reading_guide"]["coverage"]["analyzed"], [])
        with mock.patch.object(engines, "run", side_effect=[json.dumps(reply), json.dumps(synth)]) as run:
            self.assertEqual(translate.enrich_pages(self.ws, self.cfg, [1], threading.Event(), lambda *a: None), {})
        self.assertEqual(run.call_count, 2)  # manual reading completes the selected range, then synthesizes
        guide = self.ws.load("paper")["reading_guide"]
        self.assertEqual(guide["status"], "complete")
        self.assertEqual(guide["coverage"]["translated"], [1])  # 但覆盖范围如实记录：只译了 1/2 页
        self.assertFalse(guide["coverage"]["full_complete"])
        self.assertEqual(guide["summary"], "范围总结。")


if __name__ == "__main__":
    unittest.main()


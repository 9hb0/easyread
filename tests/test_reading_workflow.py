"""Reading failures, stale tasks and partial batches must preserve good translations."""
import copy
import json
import shutil
import threading
import unittest
from unittest import mock

from easyread import engines, reading, translate
from easyread.paperdata import merge_blocks, set_block_text
from tests.test_translate import make_ws


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.ws = make_ws(2)
        self.addCleanup(shutil.rmtree, self.ws.root, True)
        self.cfg = {"engine": "openai", "openai": {"vision": False},
                    "reading_enabled": True, "batch_pages": 2, "concurrency": 1}
        for name in ("locate", "materialize_figures"):
            patch = mock.patch.object(translate.pdfwork, name)
            patch.start()
            self.addCleanup(patch.stop)

    def result(self):
        return {"blocks": [{"id": f"p{p}-1", "type": "para", "page": p,
                            "en": f"Text of page {p}.", "zh": f"第{p}页的新方法。"}
                           for p in (1, 2)]}

    def run_pages(self, responder, pages=(1, 2), **cfg):
        with mock.patch.object(engines, "run", responder):
            return translate.translate_pages(self.ws, dict(self.cfg, **cfg), list(pages),
                                             threading.Event(), lambda *a: None)

    def run_enrich(self, responder, pages=(1, 2), **cfg):
        with mock.patch.object(engines, "run", responder):
            return translate.enrich_pages(self.ws, dict(self.cfg, **cfg), list(pages),
                                          threading.Event(), lambda *a: None)

    def test_reading_repair_is_frozen_and_translation_visible_first(self):
        reply = self.result()
        # Unlike an invalid highlight, a fabricated annotation source remains a page failure.
        reply["blocks"][0]["annotations"] = [{
            "type": "method_explanation", "anchor_quote": "新方法", "content": "提出了新方法。",
            "basis": "author_statement",
            "source_refs": [{"page": 1, "block": "p1-1", "quote": "invented source"}]}]
        requests = []
        def response(cfg, prompt, cwd, images=None, cancel=None):
            requests.append(prompt)
            if len(requests) <= 2:
                return json.dumps(reply)
            self.assertEqual(self.ws.load("paper")["translation"]["done_pages"], [1, 2])
            self.assertEqual(self.ws.load("paper")["blocks"][0]["zh"], "第1页的新方法。")
            self.assertEqual(self.ws.load("paper")["reading"]["pages"]["1"]["status"], "failed")
            self.assertIn("不得修改译文", prompt)
            return json.dumps({"blocks": [{"id": "p1-1", "en": "invented source", "zh": "恶意改译文",
                "highlights": [{"category": "method", "quote": "新方法"}],
                "annotations": [{"type": "method_explanation", "anchor_quote": "新方法", "content": "提出了新方法。",
                                 "basis": "author_statement",
                                 "source_refs": [{"page": 1, "block": "p1-1", "quote": "Text of page 1."}]}]}]})
        self.assertEqual(self.run_pages(response), {})
        translated = self.ws.load("paper")
        self.assertEqual(len(requests), 1)
        self.assertNotIn("annotations", translated["blocks"][0])
        self.assertEqual(translated["reading"]["pages"]["1"]["status"], "pending")
        self.assertEqual(self.run_enrich(response), {})
        paper = self.ws.load("paper")
        self.assertEqual(paper["blocks"][0]["zh"], "第1页的新方法。")
        self.assertEqual(paper["blocks"][0]["en"], "Text of page 1.")
        self.assertEqual(paper["blocks"][0]["highlights"][0]["quote"], "新方法")
        self.assertEqual(paper["blocks"][0]["annotations"][0]["source_refs"],
                         [{"page": 1, "block": "p1-1", "quote": "Text of page 1."}])
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")
        self.assertEqual(len(requests), 3)  # translate, manual enrich, one source repair
        metrics = [json.loads(line) for line in (self.ws.root / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([entry["kind"] for entry in metrics], ["translate", "enrich", "reading_repair"])

    def test_failed_reading_repair_retains_translation_and_retryable_state(self):
        reply = self.result()
        reply["blocks"][0]["annotations"] = [{
            "type": "concept", "anchor_quote": "新方法", "content": "解释方法。", "basis": "author_statement",
            "source_refs": [{"page": 9, "block": "p1-1", "quote": "Text of page 1."}]}]
        self.assertEqual(self.run_pages(lambda *a, **kw: json.dumps(reply)), {})
        before = copy.deepcopy(self.ws.load("paper")["blocks"])
        responses = iter([json.dumps(reply), engines.EngineError("reading repair failed")])
        def response(*a, **kw):
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result
        self.assertEqual(set(self.run_enrich(response)), {1})
        paper = self.ws.load("paper")
        self.assertEqual(paper["blocks"], before)
        self.assertEqual(paper["translation"]["done_pages"], [1, 2])
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "failed")
        self.assertEqual(paper["reading"]["pages"]["2"]["status"], "completed")
        self.assertIn(1, reading.pages_needing_reading(paper))
        self.assertIn("高亮错误=0，批注错误=1，研究要点错误=0",
                      (self.ws.root / "job.log").read_text(encoding="utf-8"))
        self.assertEqual(paper["reading_guide"]["coverage"]["analyzed"], [2])

    def test_invalid_annotation_and_insight_sources_fail_only_the_owned_page(self):
        invalid_refs = [
            {"page": 9, "block": "p1-1", "quote": "Text of page 1."},
            {"page": 1, "block": "fake", "quote": "Text of page 1."},
            {"page": 1, "block": "p1-1", "quote": "fabricated evidence"},
            {"page": True, "block": "p1-1", "quote": "Text of page 1."}]
        for kind in ("annotation", "insight"):
            for source_ref in invalid_refs:
                with self.subTest(kind=kind, source_ref=source_ref):
                    self.assertEqual(self.run_pages(lambda *a, **kw: json.dumps(self.result())), {})
                    reply = {"blocks": [{"id": "p1-1"}, {"id": "p2-1", "highlights": [
                        {"category": "method", "quote": "新方法"}]}]}
                    if kind == "annotation":
                        reply["blocks"][0]["annotations"] = [{
                            "type": "concept", "anchor_quote": "新方法", "content": "需要有效出处。",
                            "basis": "source_supported", "source_refs": [source_ref]}]
                    else:
                        reply["insights"] = [{"type": "method", "content": "需要有效出处。", "block": "p1-1",
                                              "status": "supported", "source_refs": [source_ref]}]
                    repair_reply = copy.deepcopy(reply)
                    repair_reply["blocks"] = repair_reply["blocks"][:1]
                    with mock.patch.object(engines, "run", side_effect=[json.dumps(reply), json.dumps(repair_reply)]) as run:
                        failed = translate.enrich_pages(self.ws, self.cfg, [1, 2], threading.Event(), lambda *a: None)
                    self.assertEqual(set(failed), {1})
                    self.assertEqual(run.call_count, 2)
                    paper = self.ws.load("paper")
                    self.assertEqual([b["zh"] for b in paper["blocks"]], ["第1页的新方法。", "第2页的新方法。"])
                    self.assertNotIn("annotations", paper["blocks"][0])
                    self.assertEqual(paper["reading"].get("insights", []), [])
                    self.assertEqual(paper["reading"]["pages"]["1"]["status"], "failed")
                    self.assertEqual(paper["reading"]["pages"]["2"]["status"], "completed")
                    self.assertEqual(paper["blocks"][1]["highlights"][0]["quote"], "新方法")
                    self.assertEqual(paper["reading_guide"]["coverage"]["analyzed"], [2])

    def test_optional_highlight_errors_keep_valid_highlight_without_repair(self):
        self.assertEqual(self.run_pages(lambda *a, **kw: json.dumps(self.result())), {})
        reply = {"blocks": [{"id": "p1-1", "highlights": [
            {"category": "method", "quote": "新方法", "importance": {}, "reason": {"invalid": True}}]}]}
        with mock.patch.object(engines, "run", return_value=json.dumps(reply)) as run:
            failed = translate.enrich_pages(self.ws, self.cfg, [1], threading.Event(), lambda *a: None)
        self.assertEqual(failed, {})
        self.assertEqual(run.call_count, 1)
        paper = self.ws.load("paper")
        highlight = paper["blocks"][0]["highlights"][0]
        self.assertEqual(highlight["quote"], "新方法")
        self.assertNotIn("importance", highlight)
        self.assertNotIn("reason", highlight)
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")
        self.assertIn("高亮错误=2，批注错误=0，研究要点错误=0",
                      (self.ws.root / "job.log").read_text(encoding="utf-8"))

    def test_one_missing_page_does_not_discard_good_page(self):
        reply = self.result()
        reply["blocks"] = reply["blocks"][:1]
        failed = self.run_pages(lambda *a, **kw: json.dumps(reply))
        self.assertEqual(sorted(failed), [2])
        self.assertEqual(self.ws.load("paper")["translation"]["done_pages"], [1])

    def test_unknown_model_source_id_is_rejected(self):
        reply = self.result()
        reply["blocks"][0]["id"] = "made-up"
        failed = self.run_pages(lambda *a, **kw: json.dumps(reply))
        self.assertEqual(sorted(failed), [1])
        self.assertEqual([b["id"] for b in self.ws.load("paper")["blocks"]], ["p2-1"])

    def test_concurrent_newer_translation_wins(self):
        newer = self.result()
        newer["blocks"][0]["zh"] = "较新的译文。"
        nested = False
        def response(*a, **kw):
            nonlocal nested
            if not nested:
                nested = True
                with mock.patch.object(engines, "run", lambda *a, **kw: json.dumps(newer)):
                    translate.translate_pages(self.ws, self.cfg, [1, 2], threading.Event(), lambda *a: None)
            return json.dumps(self.result())
        self.run_pages(response)
        self.assertEqual(self.ws.load("paper")["blocks"][0]["zh"], "较新的译文。")

    def test_cancelled_response_is_not_committed(self):
        ev = threading.Event()
        def response(*a, **kw):
            ev.set()
            return json.dumps(self.result())
        with mock.patch.object(engines, "run", response):
            with self.assertRaises(engines.Cancelled):
                translate.translate_pages(self.ws, self.cfg, [1, 2], ev, lambda *a: None)
        self.assertEqual(self.ws.load("paper")["blocks"], [])

    def test_disabled_reading_strips_unsolicited_fields(self):
        reply = self.result()
        reply["blocks"][0]["highlights"] = [{"category": "method", "quote": "新方法"}]
        self.run_pages(lambda *a, **kw: json.dumps(reply), reading_enabled=False)
        self.assertNotIn("highlights", self.ws.load("paper")["blocks"][0])

    def test_unknown_enrich_response_is_failed(self):
        merge_blocks(self.ws, self.result(), done=[1, 2])
        with mock.patch.object(engines, "run", lambda *a, **kw: '{"blocks":[{"id":"fake"}]}'):
            failed = translate.enrich_pages(self.ws, self.cfg, [1], None, lambda *a: None)
        self.assertEqual(list(failed), [1])
        self.assertEqual(self.ws.load("paper")["reading"]["pages"]["1"]["status"], "failed")

    def test_insufficient_balance_stops_pending_enrich_calls_and_preserves_text(self):
        merge_blocks(self.ws, self.result(), done=[1, 2])
        original = copy.deepcopy(self.ws.load("paper")["blocks"])
        with mock.patch.object(engines, "run", side_effect=engines.EngineError(
                "Claude Code 出错：Failed to authenticate. API Error: 403 insufficient balance")) as run:
            failed = translate.enrich_pages(self.ws, dict(self.cfg, batch_pages=1), [1, 2], None, lambda *a: None)
        self.assertEqual(set(failed), {1, 2})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.ws.load("paper")["blocks"], original)

    def test_enrich_cannot_overwrite_changed_translation(self):
        merge_blocks(self.ws, self.result(), done=[1, 2])
        def response(*a, **kw):
            set_block_text(self.ws, "p1-1", "这是修改后的新方法。")
            return '{"blocks":[{"id":"p1-1","highlights":[{"category":"method","quote":"新方法"}]}]}'
        with mock.patch.object(engines, "run", response):
            failed = translate.enrich_pages(self.ws, self.cfg, [1], None, lambda *a: None)
        self.assertEqual(set(failed), {1})
        self.assertEqual(self.ws.load("paper")["blocks"][0]["zh"], "这是修改后的新方法。")
        self.assertNotIn("highlights", self.ws.load("paper")["blocks"][0])

    def test_concurrent_newer_enrich_wins(self):
        self.assertEqual(self.run_pages(lambda *a, **kw: json.dumps(self.result())), {})
        nested = False
        def response(*a, **kw):
            nonlocal nested
            if not nested:
                nested = True
                newer = {"blocks": [{"id": "p1-1", "highlights": [{"category": "method", "quote": "新方法"}]}]}
                self.assertEqual(self.run_enrich(lambda *a, **kw: json.dumps(newer), pages=[1]), {})
            return '{"blocks":[{"id":"p1-1","highlights":[{"category":"method","quote":"第1页"}]}]}'
        self.assertEqual(set(self.run_enrich(response, pages=[1])), {1})
        paper = self.ws.load("paper")
        self.assertEqual(paper["blocks"][0]["zh"], "第1页的新方法。")
        self.assertEqual([h["quote"] for h in paper["blocks"][0]["highlights"]], ["新方法"])
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")

    def test_source_change_during_enrich_rejects_stale_annotations(self):
        self.assertEqual(self.run_pages(lambda *a, **kw: json.dumps(self.result())), {})
        before = copy.deepcopy(self.ws.load("paper")["blocks"])
        def response(*a, **kw):
            (self.ws.root / "extract" / "page-001.txt").write_text("New source text.", encoding="utf-8")
            return '{"blocks":[{"id":"p1-1","highlights":[{"category":"method","quote":"新方法"}]}]}'
        self.assertEqual(set(self.run_enrich(response, pages=[1])), {1})
        self.assertEqual(self.ws.load("paper")["blocks"], before)

    def test_cancelled_manual_enrich_is_not_committed(self):
        self.assertEqual(self.run_pages(lambda *a, **kw: json.dumps(self.result())), {})
        before = copy.deepcopy(self.ws.load("paper"))
        cancel = threading.Event()
        def response(*a, **kw):
            cancel.set()
            return '{"blocks":[{"id":"p1-1","highlights":[{"category":"method","quote":"新方法"}]}]}'
        with mock.patch.object(engines, "run", response):
            with self.assertRaises(engines.Cancelled):
                translate.enrich_pages(self.ws, self.cfg, [1], cancel, lambda *a: None)
        paper = self.ws.load("paper")
        self.assertEqual(paper["blocks"], before["blocks"])
        self.assertEqual(paper["reading"], before["reading"])

    def test_manual_synthesis_cancellation_preserves_completed_reading(self):
        self.assertEqual(self.run_pages(lambda *a, **kw: json.dumps(self.result())), {})
        reply = {"blocks": [{"id": "p1-1"}], "reviewed_pages": [1, 2],
                 "insights": [{"type": "method", "content": "提出新方法。", "block": "p1-1", "status": "provisional",
                               "source_refs": [{"page": 1, "block": "p1-1", "quote": "Text of page 1."}]}]}
        with mock.patch.object(engines, "run", side_effect=[json.dumps(reply), engines.Cancelled()]) as run:
            with self.assertRaises(engines.Cancelled):
                translate.enrich_pages(self.ws, self.cfg, [1, 2], threading.Event(), lambda *a: None)
        self.assertEqual(run.call_count, 2)
        paper = self.ws.load("paper")
        self.assertEqual([paper["reading"]["pages"][str(p)]["status"] for p in (1, 2)], ["completed", "completed"])
        self.assertEqual([b["zh"] for b in paper["blocks"]], ["第1页的新方法。", "第2页的新方法。"])
        self.assertFalse(paper["reading_guide"].get("synth_of"))

    def test_manual_enrich_preserves_user_notes_edits_and_progress(self):
        self.ws.apply_reader_ops([
            {"op": "edit", "block": "p1-1", "zh": "用户自己的译文", "at": "2026-10-13T10:00:00Z"},
            {"op": "note", "note": {"id": "n1", "block": "p1-1", "body": "保留这条笔记",
                                      "updated": "2026-10-13T10:00:00Z"}},
            {"op": "paper_note", "body": "整篇阅读记录", "at": "2026-10-13T10:00:00Z"},
            {"op": "progress", "block": "p2-1", "ratio": 0.5, "at": "2026-10-13T10:00:00Z"}])
        original = self.ws.reader_path.read_bytes()
        self.assertEqual(self.run_pages(lambda *a, **kw: json.dumps(self.result())), {})
        self.assertEqual(self.ws.reader_path.read_bytes(), original)
        reply = {"blocks": [{"id": "p1-1", "zh": "恶意改译文", "highlights": [
            {"category": "method", "quote": "新方法"}]}], "reader": {"notes": {}}}
        self.assertEqual(self.run_enrich(lambda *a, **kw: json.dumps(reply)), {})
        self.assertEqual(self.ws.reader_path.read_bytes(), original)
        self.assertEqual(self.ws.load("paper")["blocks"][0]["zh"], "第1页的新方法。")

    def test_malformed_enrich_json_gets_one_frozen_reading_repair(self):
        merge_blocks(self.ws, self.result(), done=[1, 2])
        original = copy.deepcopy(self.ws.load("paper")["blocks"])
        reply = {"blocks": [{"id": "p1-1", "zh": "不允许的改写",
                             "highlights": [{"category": "method", "quote": "新方法"}]}]}
        with mock.patch.object(engines, "run", side_effect=['{"blocks": [broken', json.dumps(reply)]):
            failed = translate.enrich_pages(self.ws, self.cfg, [1], threading.Event(), lambda *a: None)
        self.assertEqual(failed, {})
        paper = self.ws.load("paper")
        self.assertEqual(paper["blocks"][0]["zh"], original[0]["zh"])
        self.assertEqual(paper["blocks"][1], original[1])
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "completed")
        metrics = [json.loads(line) for line in (self.ws.root / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["kind"] for r in metrics], ["enrich", "reading_repair"])

    def test_second_malformed_enrich_json_stays_failed_without_more_calls(self):
        merge_blocks(self.ws, self.result(), done=[1, 2])
        original = copy.deepcopy(self.ws.load("paper")["blocks"])
        with mock.patch.object(engines, "run", side_effect=['{"blocks": [broken', 'still not JSON']):
            failed = translate.enrich_pages(self.ws, self.cfg, [1], threading.Event(), lambda *a: None)
        self.assertEqual(set(failed), {1})
        paper = self.ws.load("paper")
        self.assertEqual(paper["blocks"], original)
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "failed")
        metrics = [json.loads(line) for line in (self.ws.root / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(metrics), 2)

    def test_formula_or_table_revision_invalidates_analysis(self):
        for field, before, after in (("tex", "x=1", "x=2"), ("rows", [["90%"]], [["95%"]])):
            with self.subTest(field=field):
                block = {"id": "p1-1", "type": "math" if field == "tex" else "table", "page": 1,
                         "caption_zh": "结果", field: before}
                paper = {"meta": {"page_count": 1}, "translation": {"done_pages": [1], "source_versions": {"1": "src"}}, "blocks": [block]}
                reading.apply_reading_state(paper, [1], "completed", True)
                block[field] = after
                self.assertEqual(reading.pages_needing_reading(paper), [1])

    def test_retry_preserves_other_valid_annotations(self):
        data = self.result()
        data["blocks"][0]["highlights"] = [{"category": "method", "quote": "新方法", "block_id": "p1-1"}]
        merge_blocks(self.ws, data, done=[1, 2])
        clean = translate._clean_enrich_items(self.ws, [{"id": "p2-1", "highlights": []}], [1, 2], preserve=True)
        self.assertEqual(clean[0]["highlights"][0]["quote"], "新方法")


if __name__ == "__main__":
    unittest.main()

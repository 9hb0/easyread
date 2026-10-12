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

    def test_reading_repair_is_frozen_and_translation_visible_first(self):
        reply = self.result()
        reply["blocks"][0]["highlights"] = [{"category": "method", "quote": "不存在"}]
        requests = []
        def response(cfg, prompt, cwd, images=None, cancel=None):
            requests.append(prompt)
            if len(requests) == 1:
                return json.dumps(reply)
            self.assertEqual(self.ws.load("paper")["translation"]["done_pages"], [1, 2])
            self.assertIn("不得修改译文", prompt)
            return json.dumps({"blocks": [{"id": "p1-1", "zh": "恶意改译文",
                               "highlights": [{"category": "method", "quote": "新方法"}]}]})
        self.assertEqual(self.run_pages(response), {})
        paper = self.ws.load("paper")
        self.assertEqual(paper["blocks"][0]["zh"], "第1页的新方法。")
        self.assertEqual(paper["blocks"][0]["highlights"][0]["quote"], "新方法")
        self.assertEqual(len(requests), 2)

    def test_failed_reading_repair_retains_translation_and_retryable_state(self):
        reply = self.result()
        reply["blocks"][0]["highlights"] = [{"category": "wrong", "quote": "新方法"}]
        responses = iter([json.dumps(reply), engines.EngineError("reading repair failed")])
        def response(*a, **kw):
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result
        self.assertEqual(self.run_pages(response), {})
        paper = self.ws.load("paper")
        self.assertEqual(paper["translation"]["done_pages"], [1, 2])
        self.assertEqual(paper["reading"]["pages"]["1"]["status"], "failed")
        self.assertIn(1, reading.pages_needing_reading(paper))

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

    def test_enrich_cannot_overwrite_changed_translation(self):
        merge_blocks(self.ws, self.result(), done=[1, 2])
        def response(*a, **kw):
            set_block_text(self.ws, "p1-1", "这是修改后的新方法。")
            return '{"blocks":[{"id":"p1-1","highlights":[{"category":"method","quote":"新方法"}]}]}'
        with mock.patch.object(engines, "run", response):
            translate.enrich_pages(self.ws, self.cfg, [1], None, lambda *a: None)
        self.assertNotIn("highlights", self.ws.load("paper")["blocks"][0])

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

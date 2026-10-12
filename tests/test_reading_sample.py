"""文档里的精读样例必须仍然通过真实校验器，避免示例随代码演进而失效。"""
import json
import unittest
from pathlib import Path

from easyread import reading


SAMPLE = Path(__file__).resolve().parents[1] / "docs" / "reading-sample.json"


class ReadingSampleTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def test_sample_highlights_and_annotations_still_validate(self):
        self.assertEqual(reading.validate_highlights(self.data["blocks"]), [])
        self.assertEqual(reading.validate_annotations(self.data["blocks"]), [])
        for block in self.data["blocks"]:
            self.assertEqual(reading.validate_highlights([block]), [])

    def test_sample_insights_and_guide_resolve_to_real_blocks(self):
        insights, problems = reading.validate_insights(self.data["insights"], self.data["blocks"])
        self.assertEqual(problems, [])
        self.assertEqual([(i["type"], i["content"]) for i in insights],
                         [(i["type"], i["content"]) for i in self.data["insights"]])
        guide = self.data["reading_guide"]
        ids = {i["id"] for i in self.data["insights"]}
        for key in ("problems", "methods", "innovations", "evidence", "limitations"):
            self.assertTrue(set(guide.get(key) or []) <= ids)
        self.assertFalse(guide["coverage"]["full_complete"])
        self.assertNotEqual(guide["status"], "complete")

    def test_every_sample_source_quote_is_verbatim_in_its_block(self):
        by_id = {b["id"]: b for b in self.data["blocks"]}
        refs = [ref for block in self.data["blocks"] for ann in block.get("annotations", []) for ref in ann["source_refs"]]
        refs += [ref for insight in self.data["insights"] for ref in insight["source_refs"]]
        self.assertGreater(len(refs), 3)
        for ref in refs:
            self.assertIn(ref["quote"], reading._source_text(by_id[ref["block"]]))

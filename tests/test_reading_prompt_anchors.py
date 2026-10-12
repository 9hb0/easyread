"""Repair prompts must offer copyable anchors without rewriting fixed text."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from easyread import prompts, reading
from easyread.store import Workspace, write_json_atomic


class RepairAnchorsTest(unittest.TestCase):
    def test_repair_candidates_preserve_glosses_and_exclude_formula_boundaries(self):
        blocks = [{"id": "p6-b4", "page": 6, "type": "para",
                   "zh": "自注意力（self-attention）可能混合外观。用 $\\Phi$ 调制软偏置。",
                   "en": "Do not cite this rewritten English.",
                   "source_en": "Self-attention can mix features. We use $\\Phi$ to route cues."},
                  {"id": "l6", "page": 6, "type": "list",
                   "items": [{"zh": "第一条", "en": "First."}, {"zh": "第二条", "en": "Second."}]},
                  {"id": "eq6", "page": 6, "type": "math", "tex": "x=y"}]
        original = copy.deepcopy(blocks)
        with tempfile.TemporaryDirectory() as folder:
            ws = Workspace(Path(folder))
            write_json_atomic(ws.root / "paper.json", {"blocks": blocks})
            prompt = prompts.repair_reading(ws, [6], blocks, ["p6-b4：锚点无法定位"])
        marker = "可逐字复制的定位片段：\n"
        self.assertIn(marker, prompt)
        candidates = json.loads(prompt.split(marker, 1)[1])
        by_id = {b["id"]: b for b in candidates}
        self.assertIn("自注意力（self-attention）可能混合外观。用", by_id["p6-b4"]["zh_segments"])
        self.assertEqual(by_id["l6"]["zh_segments"], ["第一条", "第二条"])
        self.assertEqual(by_id["eq6"]["zh_segments"], [])
        self.assertNotIn("rewritten", " ".join(by_id["p6-b4"]["source_segments"]))
        for b in blocks:
            for quote in by_id[b["id"]]["zh_segments"]:
                annotated = dict(b, highlights=[{"category": "method", "quote": quote}])
                self.assertEqual(reading.validate_highlights([annotated]), [])
            for quote in by_id[b["id"]]["source_segments"]:
                self.assertIn(quote, reading._source_text(b))
        self.assertEqual(blocks, original)


if __name__ == "__main__":
    unittest.main()

"""Local highlight failures must not invalidate strict, valid page evidence."""
import unittest

from easyread import translate


class ReadingQualityPipelineTest(unittest.TestCase):
    def test_one_bad_highlight_does_not_fail_page_or_discard_good_one(self):
        b = {"id": "p1-1", "page": 1, "type": "para", "zh": "新方法提高准确率。",
             "en": "The new method improves accuracy.", "highlights": [
                 {"category": "method", "quote": "新方法"},
                 {"category": "method", "quote": "不存在"}]}
        data = {"blocks": [b], "insights": []}
        problems, bad = translate._reading_check(data, [1])
        self.assertTrue(problems)
        self.assertEqual(bad, set())
        self.assertEqual(len(b["highlights"]), 1)

    def test_optional_score_error_is_logged_but_highlight_survives(self):
        b = {"id": "p1-1", "page": 1, "type": "para", "zh": "新方法提高准确率。",
             "en": "The new method improves accuracy.", "highlights": [
                 {"category": "method", "quote": "新方法", "importance": True, "reason": {}}]}
        problems, bad = translate._reading_check({"blocks": [b], "insights": []}, [1])
        self.assertTrue(problems)
        self.assertEqual(bad, set())
        self.assertNotIn("importance", b["highlights"][0])
        self.assertNotIn("reason", b["highlights"][0])

    def test_bad_source_annotation_still_fails_page(self):
        b = {"id": "p1-1", "page": 1, "type": "para", "zh": "新方法提高准确率。",
             "en": "The new method improves accuracy.", "annotations": [
                 {"type": "method_explanation", "anchor_quote": "新方法", "content": "解释",
                  "basis": "author_statement", "source_refs": [{"page": 1, "block": "p1-1", "quote": "fabricated"}]}]}
        problems, bad = translate._reading_check({"blocks": [b], "insights": []}, [1])
        self.assertTrue(problems)
        self.assertEqual(bad, {1})
        self.assertNotIn("annotations", b)

    def test_cap_ranks_scores_then_restores_text_order(self):
        fragments = [f"片段{n}。" for n in range(7)]
        b = {"id": "p1-1", "type": "para", "zh": "".join(fragments), "highlights": [
            {"quote": fragments[n], "occurrence": 0, "importance": n % 5 + 1} for n in reversed(range(7))]}
        self.assertEqual(translate._cap_density([b]), 1)
        self.assertEqual([h["quote"] for h in b["highlights"]], fragments[1:])

    def test_cap_without_scores_keeps_legacy_model_order(self):
        b = {"highlights": [{"quote": str(n)} for n in reversed(range(7))]}
        translate._cap_density([b])
        self.assertEqual([h["quote"] for h in b["highlights"]], [str(n) for n in range(6, 0, -1)])


if __name__ == "__main__":
    unittest.main()

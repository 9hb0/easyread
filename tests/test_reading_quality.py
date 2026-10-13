"""Deterministic, non-destructive quality derivation; no model calls."""
import copy
import unittest

from easyread import reading


def fixture():
    quote = "本文根据输入动态分配计算路径，从而减少计算开销。"
    blocks = [{"id": f"p{p}-1", "page": p, "type": "para", "zh": quote,
               "en": "We dynamically allocate computation based on inputs.",
               "highlights": [{"block_id": f"p{p}-1", "category": "method",
                               "quote": quote, "occurrence": 0, "importance": score}]}
              for p, score in ((1, 4), (2, 5))]
    paper = {"blocks": blocks, "translation": {"done_pages": [1, 2],
             "source_versions": {"1": "src1", "2": "src2"}}, "meta": {"page_count": 2}}
    reading.apply_reading_state(paper, [1, 2], "completed", True)
    return paper


class ReadingQualityTest(unittest.TestCase):
    def quality(self):
        from easyread import reading_quality
        return reading_quality

    def test_duplicate_prefers_score_and_never_changes_candidates(self):
        q = self.quality()
        p = fixture()
        before = copy.deepcopy(p["blocks"])
        q.rebuild_quality(p)
        entries = p["reading"]["quality"]["suppressed"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["key"], q.highlight_stable_key("p1-1", p["blocks"][0]["highlights"][0]))
        self.assertEqual(entries[0]["canonical_key"], q.highlight_stable_key("p2-1", p["blocks"][1]["highlights"][0]))
        self.assertEqual(p["blocks"], before)
        self.assertEqual(entries[0]["reason"], "exact_duplicate")

    def test_quality_is_idempotent_and_ties_keep_document_order(self):
        q = self.quality()
        p = fixture()
        p["blocks"][1]["highlights"][0]["importance"] = 4
        q.rebuild_quality(p)
        first = copy.deepcopy(p["reading"]["quality"])
        q.rebuild_quality(p)
        self.assertEqual(p["reading"]["quality"], first)
        self.assertTrue(first["suppressed"][0]["key"].startswith("p2-1|"))

    def test_ties_prefer_body_over_abstract_and_missing_source_never_suppresses(self):
        q = self.quality()
        p = fixture()
        p["blocks"][0]["role"] = "abstract"
        p["blocks"][1]["highlights"][0]["importance"] = 4
        q.rebuild_quality(p)
        self.assertTrue(p["reading"]["quality"]["suppressed"][0]["key"].startswith("p1-1|"))
        p["blocks"][1]["en"] = ""
        reading.apply_reading_state(p, [2], "completed", True)
        q.rebuild_quality(p)
        self.assertEqual(p["reading"]["quality"]["suppressed"], [])

    def test_different_conditions_numbers_categories_and_short_phrases_stay(self):
        q = self.quality()
        for suffix, category in (("性能提高4.2%。", "method"), ("只适用于低分辨率输入。", "method"),
                                 ("", "innovation")):
            p = fixture()
            p["blocks"][1]["zh"] += suffix
            p["blocks"][1]["highlights"][0].update(quote=p["blocks"][1]["zh"], category=category)
            reading.apply_reading_state(p, [2], "completed", True)
            q.rebuild_quality(p)
            self.assertEqual(p["reading"]["quality"]["suppressed"], [])
        p = fixture()
        for b in p["blocks"]:
            b["zh"] = b["highlights"][0]["quote"] = "准确率"
        reading.apply_reading_state(p, [1, 2], "completed", True)
        q.rebuild_quality(p)
        self.assertEqual(p["reading"]["quality"]["suppressed"], [])

    def test_quality_hash_expires_for_every_dependency(self):
        q = self.quality()
        mutations = [lambda p: p["blocks"][0].update(zh="修改译文"),
                     lambda p: p["blocks"][0]["highlights"][0].update(importance=5),
                     lambda p: p["translation"]["source_versions"].update({"1": "new"}),
                     lambda p: p["reading"]["pages"]["1"].update(status="failed"),
                     lambda p: p["reading"]["pages"]["1"].update(prompt_version=99),
                     lambda p: p["blocks"][0]["highlights"][0].update(occurrence=1),
                     lambda p: p["blocks"][0].update(role="abstract"),
                     lambda p: p["blocks"][0]["highlights"][0].update(block_id="wrong-owner"),
                     lambda p: p["blocks"][0]["highlights"][0].update(source_refs=[])]
        for mutate in mutations:
            p = fixture()
            q.rebuild_quality(p)
            before = p["reading"]["quality"]["input_hash"]
            mutate(p)
            self.assertNotEqual(q.quality_input_hash(p), before)

    def test_failed_or_partial_pages_cannot_suppress_good_page(self):
        q = self.quality()
        p = fixture()
        p["reading"]["pages"]["2"]["status"] = "failed"
        q.rebuild_quality(p)
        self.assertEqual(p["reading"]["quality"]["suppressed"], [])

    def test_invalid_source_or_overlap_cannot_become_canonical(self):
        q = self.quality()
        for kind in ("source", "overlap"):
            with self.subTest(kind=kind):
                p = fixture()
                h = p["blocks"][1]["highlights"][0]
                if kind == "source":
                    h["source_refs"] = [{"page": 2, "block": "p2-1", "quote": "invented"}]
                else:
                    p["blocks"][1]["highlights"].append(dict(h, category="innovation"))
                q.rebuild_quality(p)
                self.assertEqual(p["reading"]["quality"]["suppressed"], [])
        p["reading"]["pages"]["2"]["status"] = "completed"
        p["translation"]["done_pages"] = [1]
        q.rebuild_quality(p)
        self.assertEqual(p["reading"]["quality"]["suppressed"], [])

    def test_key_is_independent_of_model_id_and_preserves_occurrence(self):
        q = self.quality()
        h = fixture()["blocks"][0]["highlights"][0]
        before = q.highlight_stable_key("p1-1", h)
        h["id"] = "changed-model-id"
        self.assertEqual(q.highlight_stable_key("p1-1", h), before)
        h["occurrence"] = 1
        self.assertNotEqual(q.highlight_stable_key("p1-1", h), before)


if __name__ == "__main__":
    unittest.main()

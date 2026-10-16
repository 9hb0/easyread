"""Known page numbers may be omitted; every body source still needs coverage."""
import copy
import unittest

from easyread import translate


class CoverageTest(unittest.TestCase):
    def source(self, footer_box=None, footer="2"):
        return {"blocks": [
            {"id": "p2-1", "page": 2, "en": "Body text.", "box": [.1, .1, .8, .2]},
            {"id": "p2-2", "page": 2, "en": footer, "box": footer_box}]}

    def result(self, source):
        return translate._normalize({"blocks": [{
            "id": "p2-1", "page": 2, "type": "para", "zh": "正文。"}]}, [2], set(), source)

    def test_proven_centered_footer_number_does_not_fail_a_complete_page(self):
        source = self.source([.495, .9345, .505, .9483])
        before = copy.deepcopy(source)
        self.assertEqual(translate._translation_pages(self.result(source), source, [2]), [2])
        self.assertEqual(source, before)

    def test_body_or_unpositioned_numeral_is_not_automatically_skipped(self):
        for box, text in [(None, "2"), ([.495, .4, .505, .42], "2"),
                          ([.1, .9345, .11, .9483], "2"),
                          ([.495, .9345, .505, .9483], "3"),
                          ([.495, .9345, .505, .9483], "2 + 2")]:
            with self.subTest(box=box, text=text):
                source = self.source(box, text)
                self.assertEqual(translate._translation_pages(self.result(source), source, [2]), [])

    def test_footer_skip_cannot_hide_missing_body_text(self):
        source = self.source([.495, .9345, .505, .9483])
        source["blocks"].append({"id": "p2-3", "page": 2, "en": "Another paragraph.", "box": [.1, .3, .8, .4]})
        self.assertEqual(translate._translation_pages(self.result(source), source, [2]), [])

    def bibliography(self):
        return {"blocks": [
            {"id": "p2-1", "page": 2, "en": "[17] A. Author.", "box": None},
            {"id": "p2-2", "page": 2, "en": "A wrapped\nreference title. 2025.", "box": None},
            {"id": "p2-3", "page": 2, "en": "2", "box": [.49, .93, .51, .95]},
            {"id": "p3-1", "page": 3, "en": "[18] B. Author. Another reference. 2026.", "box": None},
            {"id": "p3-2", "page": 3, "en": "3", "box": [.49, .93, .51, .95]}]}

    def reference_response(self):
        return {"blocks": [], "references": [
            {"id": "17", "text": "A. Author. A wrapped reference title. 2025."},
            {"id": "18", "text": "[18] B. Author. Another reference. 2026."}]}

    def test_reference_only_continuation_pages_get_source_backed_blocks(self):
        source = self.bibliography()
        before = copy.deepcopy(source)
        result = translate._normalize(self.reference_response(), [2, 3], set(), source)
        self.assertEqual(translate._translation_pages(result, source, [2, 3]), [2, 3])
        self.assertEqual([(b["id"], b["page"], b["type"], b["source_ids"]) for b in result["blocks"]], [
            ("p2-1", 2, "references", ["p2-1", "p2-2"]),
            ("p3-1", 3, "references", ["p3-1"])])
        self.assertEqual(result["blocks"][0]["source_en"], "[17] A. Author.\nA wrapped\nreference title. 2025.")
        self.assertEqual(source, before)

    def test_missing_or_truncated_reference_still_fails_its_page(self):
        for references in ([{"id": "17", "text": "A. Author."}],
                           [{"id": "19", "text": "A. Author. A wrapped reference title. 2025."}],
                           [None, {"id": "17", "text": ""}]):
            with self.subTest(references=references):
                response = self.reference_response()
                response["references"] = references + response["references"][1:]
                source = self.bibliography()
                result = translate._normalize(response, [2, 3], set(), source)
                self.assertEqual(translate._translation_pages(result, source, [2, 3]), [3])

    def test_reference_metadata_cannot_hide_untranslated_body_on_mixed_page(self):
        source = self.bibliography()
        source["blocks"].insert(0, {"id": "p2-body", "page": 2, "en": "Acknowledgments and funding.", "box": None})
        response = self.reference_response()
        result = translate._normalize(response, [2, 3], set(), source)
        self.assertEqual(translate._translation_pages(result, source, [2, 3]), [3])
        response = self.reference_response()
        response["blocks"] = [{"id": "p2-body", "page": 2, "type": "para", "zh": "致谢与资助。"}]
        result = translate._normalize(response, [2, 3], set(), source)
        self.assertEqual(translate._translation_pages(result, source, [2, 3]), [2, 3])

    def test_existing_reference_blocks_keep_unique_source_ownership(self):
        source = self.bibliography()
        response = self.reference_response()
        response["blocks"] = [{"id": "p2-1", "page": 2, "type": "references",
                               "source_ids": ["p2-1", "p2-2"], "zh": "参考文献"}]
        result = translate._normalize(response, [2, 3], set(), source)
        covered = [i for b in result["blocks"] for i in b["source_ids"]]
        self.assertEqual(len(covered), len(set(covered)))
        self.assertEqual(translate._translation_pages(result, source, [2, 3]), [2, 3])

    def test_pdfium_discretionary_hyphen_matches_reference_text(self):
        source = self.bibliography()
        source["blocks"][1]["en"] = "A wrapped\nToken\ufffeLearner reference title. 2025."
        response = self.reference_response()
        response["references"][0]["text"] = "A. Author. A wrapped Token-Learner reference title. 2025."
        result = translate._normalize(response, [2, 3], set(), source)
        self.assertEqual(translate._translation_pages(result, source, [2, 3]), [2, 3])
        self.assertIn("Token\ufffeLearner", result["blocks"][0]["source_en"])

    def test_existing_continuation_cannot_make_truncated_reference_look_complete(self):
        for text, valid in (("A. Author.", [3]),
                            ("A. Author. A wrapped reference title. 2025.", [2, 3])):
            with self.subTest(text=text):
                source = self.bibliography()
                response = self.reference_response()
                response["references"][0]["text"] = text
                response["blocks"] = [{"id": "p2-2", "page": 2, "type": "references", "zh": "参考文献"}]
                result = translate._normalize(response, [2, 3], set(), source)
                self.assertEqual(translate._translation_pages(result, source, [2, 3]), valid)

    def test_recovered_references_keep_order_around_existing_blocks(self):
        source = {"blocks": [{"id": f"p2-{i}", "page": 2, "en": f"[{i}] Reference {i}.", "box": None}
                             for i in (1, 2, 3)]}
        response = {"blocks": [{"id": "p2-2", "page": 2, "type": "references", "zh": "参考文献"}],
                    "references": [{"id": str(i), "text": f"Reference {i}."} for i in (1, 2, 3)]}
        result = translate._normalize(response, [2], set(), source)
        self.assertEqual(translate._translation_pages(result, source, [2]), [2])
        self.assertEqual([sid for b in result["blocks"] for sid in b["source_ids"]], ["p2-1", "p2-2", "p2-3"])

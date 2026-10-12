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

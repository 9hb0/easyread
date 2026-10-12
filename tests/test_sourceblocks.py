"""原文块：完整覆盖、稳定 ID、独立版本和可靠坐标，全部离线测试。"""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from easyread import pdfwork, sourceblocks


class SourceBlocksTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="easyread-sourceblocks-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "extract").mkdir()

    def write_page(self, page, text, chars=None):
        base = self.root / "extract" / f"page-{page:03d}"
        if text is not None:
            base.with_suffix(".txt").write_text(text, encoding="utf-8")
        if chars is not None:
            base.with_suffix(".chars.json").write_text(json.dumps(chars, ensure_ascii=False), encoding="utf-8")

    def ensure(self, pages):
        return sourceblocks.ensure(self.root, pages)

    def test_prepare_creates_source_file_without_changing_translation(self):
        paper = self.root / "paper.json"
        original = b'{"blocks":[{"id":"legacy-id","zh":"saved"}],"translation":{"done_pages":[1]}}'
        paper.write_bytes(original)
        pages = [{"n": 1, "w": 600, "h": 800, "img": "pages/page-001.webp"}]
        def extract(pdf, out):
            self.write_page(1, "A source paragraph.")
            return 1
        with mock.patch.object(pdfwork, "render_pages", return_value=pages), \
                mock.patch.object(pdfwork, "extract_text", side_effect=extract):
            self.assertEqual(pdfwork.prepare(self.root), pages)
        path = self.root / "extract" / "source-blocks.json"
        self.assertTrue(path.is_file(), "prepare 必须生成原文块文件")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["blocks"][0]["id"], "p1-1")
        self.assertTrue(saved["pages"]["1"]["source_version"])
        self.assertEqual(paper.read_bytes(), original)

    def test_contract_keeps_paragraphs_and_every_nonempty_source(self):
        self.write_page(1, "  Paper title  \n\nFirst paragraph\ncontinued line.\n \nα + β = 3\n\n[1] Reference.\n\n7\n")
        result = self.ensure([1])
        self.assertEqual([b["en"] for b in result["blocks"]], [
            "Paper title", "First paragraph\ncontinued line.", "α + β = 3", "[1] Reference.", "7"])
        self.assertEqual([b["id"] for b in result["blocks"]], ["p1-1", "p1-2", "p1-3", "p1-4", "p1-5"])
        self.assertEqual(result["pages"]["1"]["blocks"], result["blocks"])
        for block in result["blocks"]:
            self.assertEqual(block["page"], 1)
            self.assertIsNone(block["box"])
        self.assertIsInstance(result["pages"]["1"]["source_version"], str)

    def test_single_newlines_do_not_fragment_unpositioned_text(self):
        self.write_page(1, "First wrapped line\nsecond wrapped line\nthird wrapped line.")
        self.assertEqual([b["en"] for b in self.ensure([1])["blocks"]], [
            "First wrapped line\nsecond wrapped line\nthird wrapped line."])

    def test_geometry_separates_paragraphs_and_keeps_wrapped_lines(self):
        self.write_page(1, "First line\nwrapped line.\nNext paragraph.", [
            ["First line", .1, .10, .8, .12],
            ["wrapped line.", .1, .13, .7, .15],
            ["Next paragraph.", .1, .22, .7, .24]])
        blocks = self.ensure([1])["blocks"]
        self.assertEqual([b["en"] for b in blocks], ["First line\nwrapped line.", "Next paragraph."])
        self.assertEqual([b["box"] for b in blocks], [[.1, .1, .8, .15], [.1, .22, .7, .24]])

    def test_same_source_reuses_ids_and_version_without_rewriting(self):
        self.write_page(1, "One.\n\nTwo.")
        first = self.ensure([1])
        path = self.root / "extract" / "source-blocks.json"
        stamp = path.stat().st_mtime_ns
        self.write_page(1, "One.\n\nTwo.")
        self.assertEqual(self.ensure([1]), first)
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_pdfium_blank_lines_and_discretionary_hyphen_keep_real_paragraphs(self):
        text = "Heading\n\nFirst wrapped line\n\nunder\ufffeconstrained continuation.\n\nNext paragraph."
        self.write_page(1, text, [
            ["Heading", .1, .05, .4, .07],
            ["First wrapped line", .1, .10, .8, .12],
            ["under-constrained continuation.", .1, .13, .8, .15],
            ["Next paragraph.", .1, .22, .7, .24]])
        blocks = self.ensure([1])["blocks"]
        self.assertEqual([b["en"] for b in blocks], [
            "Heading", "First wrapped line\n\nunder\ufffeconstrained continuation.", "Next paragraph."])
        self.assertEqual(blocks[1]["box"], [.1, .1, .8, .15])

    def test_unknown_pdfium_character_is_not_matched_to_an_arbitrary_symbol(self):
        self.write_page(1, "A\ufffeB", [["A+B", .1, .1, .8, .12]])
        self.assertIsNone(self.ensure([1])["blocks"][0]["box"])

    def test_multiline_extracted_body_does_not_absorb_a_separate_heading(self):
        self.write_page(1, "Section heading\n\nBody line and wrapped text\ncontinued text.", [
            ["Section heading", .1, .05, .4, .07],
            ["Body line and wrapped text", .1, .09, .8, .13],
            ["continued text.", .1, .133, .7, .153]])
        self.assertEqual([b["en"] for b in self.ensure([1])["blocks"]], [
            "Section heading", "Body line and wrapped text\ncontinued text."])

    def test_bullet_hanging_continuation_stays_in_the_list_source(self):
        self.write_page(1, "• First item.\n\n• Second item with wrapped\n\ncontinuation.\n\nNext paragraph.", [
            ["• First item.", .13, .10, .8, .12],
            ["• Second item with wrapped", .13, .13, .8, .15],
            ["continuation.", .15, .153, .7, .173],
            ["Next paragraph.", .1, .22, .8, .24]])
        self.assertEqual([b["en"] for b in self.ensure([1])["blocks"]], [
            "• First item.\n\n• Second item with wrapped\n\ncontinuation.", "Next paragraph."])

    def test_list_does_not_absorb_text_from_another_column(self):
        self.write_page(1, "- Left item.\n\nRight column paragraph.", [
            ["- Left item.", .083, .11, .25, .125],
            ["Right column paragraph.", .55, .128, .9, .143]])
        blocks = self.ensure([1])["blocks"]
        self.assertEqual([b["en"] for b in blocks], ["- Left item.", "Right column paragraph."])
        self.assertEqual(blocks[0]["box"], [.083, .11, .25, .125])

    def test_changed_source_changes_only_its_page_version(self):
        self.write_page(1, "Old source.")
        self.write_page(2, "Stable source.")
        before = self.ensure([1, 2])
        self.write_page(1, "New source.")
        after = self.ensure([1, 2])
        self.assertNotEqual(before["pages"]["1"]["source_version"], after["pages"]["1"]["source_version"])
        self.assertEqual(before["pages"]["2"], after["pages"]["2"])
        self.assertEqual(after["blocks"][0]["id"], "p1-1")
        self.assertEqual(after["blocks"][0]["en"], "New source.")

    def test_changed_coordinates_invalidate_source_version(self):
        self.write_page(1, "Text.", [["Text.", .1, .1, .3, .2]])
        before = self.ensure([1])
        self.write_page(1, "Text.", [["Text.", .2, .2, .4, .3]])
        after = self.ensure([1])
        self.assertNotEqual(before["pages"]["1"]["source_version"], after["pages"]["1"]["source_version"])
        self.assertEqual(after["blocks"][0]["box"], [.2, .2, .4, .3])

    def test_exact_chars_match_has_union_box(self):
        self.write_page(1, "Alpha β = 2.", [
            ["Alpha", .1, .2, .3, .25], [" β = 2.", .32, .2, .6, .25]])
        self.assertEqual(self.ensure([1])["blocks"][0]["box"], [.1, .2, .6, .25])

    def test_ligatures_and_line_spaces_map_to_actual_char_boxes(self):
        self.write_page(1, "An efficient\nmethod.", [
            ["An efﬁcient", .1, .2, .8, .22], ["method.", .1, .23, .4, .25]])
        blocks = self.ensure([1])["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["en"], "An efficient\nmethod.")
        self.assertEqual(blocks[0]["box"], [.1, .2, .8, .25])

    def test_repeated_paragraphs_have_distinct_reliable_boxes(self):
        self.write_page(1, "Same.\n\nSame.", [
            ["Same.", .1, .1, .4, .15], ["Same.", .1, .3, .4, .35]])
        self.assertEqual([b["box"] for b in self.ensure([1])["blocks"]], [
            [.1, .1, .4, .15], [.1, .3, .4, .35]])

    def test_unique_paragraphs_can_match_out_of_order_chars(self):
        self.write_page(1, "First.\n\nSecond.", [
            ["Second.", .6, .1, .9, .15], ["First.", .1, .1, .4, .15]])
        self.assertEqual([b["box"] for b in self.ensure([1])["blocks"]], [
            [.1, .1, .4, .15], [.6, .1, .9, .15]])

    def test_ambiguous_match_does_not_choose_first_occurrence(self):
        self.write_page(1, "Same.", [
            ["Same.", .1, .1, .4, .15], ["Same.", .1, .3, .4, .35]])
        self.assertIsNone(self.ensure([1])["blocks"][0]["box"])

    def test_incomplete_match_and_changed_math_symbols_never_guess_box(self):
        cases = [("Prefix missing tail.", "Prefix tail."), ("α + β = 3", "α - β = 3"),
                 ("Alpha", "Alphabet"), ("x² = 1", "x2 = 1"), ("x₂ = 1", "x2 = 1")]
        for source, glyphs in cases:
            with self.subTest(source=source):
                self.write_page(1, source, [[glyphs, .1, .2, .8, .3]])
                self.assertIsNone(self.ensure([1])["blocks"][0]["box"])

    def test_malformed_or_invalid_chars_preserve_text_without_box(self):
        cases = ["broken json", "{}", '[["Text.", 0.4, 0.1, 0.2, 0.3]]',
                 '[["Text.", -0.1, 0.1, 0.2, 0.3]]', '[["Text.", 0.1, 0.1, NaN, 0.3]]',
                 '[["Text.", "0.1", 0.1, 0.2, 0.3]]']
        self.write_page(1, "Text.")
        path = self.root / "extract" / "page-001.chars.json"
        for contents in cases:
            with self.subTest(contents=contents):
                path.write_text(contents, encoding="utf-8")
                blocks = self.ensure([1])["blocks"]
                self.assertEqual(blocks[0]["en"], "Text.")
                self.assertIsNone(blocks[0]["box"])

    def test_chars_only_source_is_not_lost(self):
        for text in (None, " \n "):
            with self.subTest(text=text):
                self.write_page(1, text, [["Only characters.", .1, .2, .6, .25]])
                block = self.ensure([1])["blocks"][0]
                self.assertEqual(block["en"], "Only characters.")
                self.assertEqual(block["box"], [.1, .2, .6, .25])

    def test_chars_only_source_keeps_paragraph_and_line_boundaries(self):
        self.write_page(1, None, [
            ["First line", .1, .10, .8, .12],
            ["wrapped line.", .1, .13, .7, .15],
            ["Next paragraph.", .1, .22, .7, .24]])
        blocks = self.ensure([1])["blocks"]
        self.assertEqual([b["en"] for b in blocks], ["First line\nwrapped line.", "Next paragraph."])
        self.assertEqual([b["box"] for b in blocks], [[.1, .1, .8, .15], [.1, .22, .7, .24]])

    def test_empty_and_missing_pages_have_empty_blocks_and_versions(self):
        self.write_page(1, " \n\t ", [])
        result = self.ensure([1, 2])
        self.assertEqual(result["blocks"], [])
        for page in ("1", "2"):
            self.assertEqual(result["pages"][page]["blocks"], [])
            self.assertTrue(result["pages"][page]["source_version"])

    def test_deleted_source_clears_stale_cached_blocks(self):
        self.write_page(1, "Old source.")
        before = self.ensure([1])
        (self.root / "extract" / "page-001.txt").unlink()
        after = self.ensure([1])
        self.assertEqual(after["blocks"], [])
        self.assertNotEqual(before["pages"]["1"]["source_version"], after["pages"]["1"]["source_version"])

    def test_partial_calls_preserve_other_cached_pages_and_return_only_requested(self):
        self.write_page(1, "One.")
        self.write_page(2, "Two.")
        self.ensure([1])
        result = self.ensure([2])
        self.assertEqual(list(result["pages"]), ["2"])
        self.assertEqual([b["id"] for b in result["blocks"]], ["p2-1"])
        saved = json.loads((self.root / "extract" / "source-blocks.json").read_text(encoding="utf-8"))
        self.assertEqual([b["id"] for b in saved["blocks"]], ["p1-1", "p2-1"])

    def test_pages_can_be_numbers_or_prepare_metadata_and_are_sorted(self):
        self.write_page(1, "One.")
        self.write_page(2, "Two.")
        result = self.ensure([{"n": 2}, 1, 2])
        self.assertEqual(list(result["pages"]), ["1", "2"])
        self.assertEqual([b["id"] for b in result["blocks"]], ["p1-1", "p2-1"])

    def test_corrupt_source_cache_is_rebuilt_from_extraction(self):
        self.write_page(1, "Current source.")
        path = self.root / "extract" / "source-blocks.json"
        path.write_text("half-written json", encoding="utf-8")
        result = self.ensure([1])
        self.assertEqual(result["blocks"][0]["en"], "Current source.")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pages"], result["pages"])

    def test_concurrent_page_batches_do_not_overwrite_other_pages(self):
        for page in range(1, 5):
            self.write_page(page, f"Source {page}.")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda p: self.ensure([p]), [4, 2, 3, 1]))
        self.assertEqual([r["blocks"][0]["page"] for r in results], [4, 2, 3, 1])
        saved = json.loads((self.root / "extract" / "source-blocks.json").read_text(encoding="utf-8"))
        self.assertEqual([b["id"] for b in saved["blocks"]], ["p1-1", "p2-1", "p3-1", "p4-1"])
        self.assertEqual(list((self.root / "extract").glob("*.tmp")), [])

    def test_real_pdf_prepare_is_repeatable_and_locates_paragraphs(self):
        # 独立的最小 PDF fixture，不依赖模型、联网或额外 PDF 写入库。
        content = (b"BT /F1 12 Tf 50 740 Td (First source line.) Tj "
                   b"0 -14 Td (Continued source line.) Tj "
                   b"0 -36 Td (Second source paragraph.) Tj ET")
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
                   b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                   b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
                   b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
                   b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                   b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = []
        for n, obj in enumerate(objects, 1):
            offsets.append(len(pdf))
            pdf.extend(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(pdf)
        pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
        for offset in offsets:
            pdf.extend(f"{offset:010d} 00000 n \n".encode())
        pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        (self.root / "source.pdf").write_bytes(pdf)
        pages = pdfwork.prepare(self.root)
        first = self.ensure([1])
        self.assertEqual(len(pages), 1)
        self.assertEqual([b["en"] for b in first["blocks"]], [
            "First source line.\nContinued source line.", "Second source paragraph."])
        self.assertTrue(all(b["box"] is not None for b in first["blocks"]))
        self.assertLess(first["blocks"][0]["box"][3], first["blocks"][1]["box"][1])
        self.assertEqual(pdfwork.prepare(self.root), pages)
        self.assertEqual(self.ensure([1]), first)


if __name__ == "__main__":
    unittest.main()

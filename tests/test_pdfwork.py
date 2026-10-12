"""原页图生成：优先用已渲染的 webp 转 JPEG，不开 PDFium。  python -m unittest tests.test_pdfwork"""
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from easyread import pdfwork


def make_root(n_pages: int = 2) -> Path:
    root = Path(tempfile.mkdtemp(prefix="easyread-pdfwork-"))
    (root / "pages").mkdir()
    for n in range(1, n_pages + 1):
        Image.new("RGB", (40, 60), (200, 30, 40)).save(root / "pages" / f"page-{n:03d}.webp", "WEBP")
    return root


class EngineImageTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_uses_rendered_webp_without_pdfium(self):
        # 目录里根本没有 source.pdf 也能出图：webp 转 JPEG，不碰 PDFium
        out = pdfwork.engine_image(self.root, 1)
        self.assertEqual(out, self.root / "extract" / "page-001.jpg")
        self.assertTrue(out.is_file())
        with Image.open(out) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertEqual(im.size, (40, 60))

    def test_existing_jpg_is_reused(self):
        pdfwork.engine_image(self.root, 1)
        (self.root / "extract" / "page-001.jpg").write_bytes(b"stub")
        out = pdfwork.engine_image(self.root, 1)
        self.assertEqual(out.read_bytes(), b"stub")  # 已有的图不重做

    def test_concurrent_calls_all_get_valid_jpg(self):
        outs, lock = [], threading.Lock()

        def grab():
            out = pdfwork.engine_image(self.root, 2)
            with lock:
                outs.append(out)

        threads = [threading.Thread(target=grab) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(outs), 6)
        with Image.open(outs[0]) as im:
            self.assertEqual(im.format, "JPEG")
        # 没留下临时文件
        self.assertEqual(list((self.root / "extract").glob("*")), [self.root / "extract" / "page-002.jpg"])


if __name__ == "__main__":
    unittest.main()

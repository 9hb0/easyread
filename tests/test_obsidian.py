import shutil
import tempfile
import unittest
from pathlib import Path

from easyread import obsidian
from easyread.library import Library
from easyread.store import write_json_atomic


class ObsidianTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lib_root = self.tmp / "library"
        self.vault = self.tmp / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.cfg = {"library_dir": str(self.lib_root), "port": 8765,
                    "obsidian": {"vault": str(self.vault), "folder": "论文", "auto": True}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def paper(self, pid="abcd11112222", title="测试: 论文标题"):
        root = self.lib_root / pid
        root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(root / "paper.json", {"meta": {"title_zh": title, "title_en": "Test Paper",
            "authors": "A, B，C", "year": "2025", "arxiv": "2509.19296"}, "blocks": []})
        write_json_atomic(root / "item.json", {"tags": ["三维"]})
        write_json_atomic(root / "deepread.json", {"state": "done", "updated": "2026-10-01T17:12:47"})
        (root / "deepread.md").write_text(
            "行内 \\(x^2\\) 公式。\n\n\\[\nL=a+b\n\\]\n\n```text\n代码 \\(不动\\)\n```", encoding="utf-8")
        return root

    def test_math_conversion(self):
        md = "行内 \\(x^2\\) 和\n\n\\[\nL=a+b\n\\]\n\n`代码 \\(x\\)`\n\n```py\n围栏 \\(x\\)\n```"
        out = obsidian.math_to_obsidian(md)
        self.assertIn("$x^2$", out)
        self.assertIn("$$\nL=a+b\n$$", out)
        self.assertIn("`代码 \\(x\\)`", out)
        self.assertIn("围栏 \\(x\\)", out)

    def test_filename_and_note(self):
        self.paper()
        ws = Library(self.lib_root).all()[0]
        self.assertEqual(obsidian.note_filename(ws), "测试 论文标题.md")
        note = obsidian.build_note(ws, self.cfg)
        self.assertIn('title: "测试: 论文标题"', note)
        self.assertIn("easyread_id: abcd11112222", note)
        self.assertIn("created: 2026-10-01", note)
        self.assertIn('"A", "B", "C"', note)
        self.assertIn("$x^2$", note)
        self.assertIn("$$\nL=a+b\n$$", note)
        self.assertIn("代码 \\(不动\\)", note)

    def test_sync_idempotent_and_rename(self):
        self.paper()
        ws = Library(self.lib_root).all()[0]
        first = obsidian.sync_paper(ws, self.cfg)
        self.assertEqual(first["status"], "written")
        old = Path(first["file"])
        self.assertTrue(old.exists())
        self.assertEqual(obsidian.sync_paper(ws, self.cfg)["status"], "unchanged")
        paper = ws.load("paper")
        paper["meta"]["title_zh"] = "新标题"
        write_json_atomic(ws.paper_path, paper)
        second = obsidian.sync_paper(ws, self.cfg)
        self.assertEqual(second["status"], "written")
        self.assertFalse(old.exists())
        self.assertTrue(Path(second["file"]).exists())

    def test_sync_all_and_missing_vault(self):
        self.paper()
        self.paper(pid="bbbb22223333")
        res = obsidian.sync_all(self.cfg)
        self.assertEqual((res["total"], res["written"]), (2, 2))
        self.assertEqual(len(list((self.vault / "论文").glob("*.md"))), 2)
        res2 = obsidian.sync_all({**self.cfg, "obsidian": {"vault": "", "folder": "论文", "auto": True}})
        self.assertEqual(res2["skipped"], 2)
        res3 = obsidian.sync_all(self.cfg, vault=str(self.tmp / "nope"))
        self.assertEqual(res3["error"], 2)

    def test_auto_sync_respects_switch(self):
        self.paper()
        ws = Library(self.lib_root).all()[0]
        obsidian.auto_sync(ws, self.cfg)
        note = self.vault / "论文" / "测试 论文标题.md"
        self.assertTrue(note.exists())
        (ws.root / "deepread.md").write_text("改成 \\(y\\) 了", encoding="utf-8")
        off = {**self.cfg, "obsidian": {"vault": str(self.vault), "folder": "论文", "auto": False}}
        obsidian.auto_sync(ws, off)
        self.assertNotIn("$y$", note.read_text(encoding="utf-8"))
        obsidian.auto_sync(ws, self.cfg)
        self.assertIn("$y$", note.read_text(encoding="utf-8"))

    def test_find_vaults(self):
        (self.tmp / "roots" / "我的库" / ".obsidian").mkdir(parents=True)
        (self.tmp / "roots" / "node_modules" / "x").mkdir(parents=True)
        found = obsidian.find_vaults(roots=[self.tmp / "roots"])
        self.assertIn(str(self.tmp / "roots" / "我的库"), found)

    def test_classify_folders_detection(self):
        for name in ("00_Foundations", "11_Generation（生成）", "待归档", "research", "tmp"):
            (self.vault / name).mkdir(exist_ok=True)
        self.assertEqual(obsidian.classify_folders(self.vault), ["00_Foundations", "11_Generation（生成）"])

    def test_classify_paper_picks_folder_and_caches(self):
        import easyread.chat_models as cm
        import easyread.engines as eng
        self.paper()
        ws = Library(self.lib_root).all()[0]
        orig_cfg, orig_run = cm.engine_cfg, eng.run
        cm.engine_cfg = lambda cfg, mid: ({}, {})
        eng.run = lambda *a, **k: "我认为放 11_生成 最合适"
        try:
            self.assertEqual(obsidian.classify_paper(ws, self.cfg, ["10_重建", "11_生成"]), "11_生成")
            self.assertEqual(obsidian.cached_folder(ws, ["10_重建", "11_生成"]), "11_生成")
        finally:
            cm.engine_cfg, eng.run = orig_cfg, orig_run

    def test_sync_classifies_once_and_moves_file(self):
        import easyread.chat_models as cm
        import easyread.engines as eng
        self.paper()
        (self.vault / "10_重建").mkdir()
        (self.vault / "11_生成").mkdir()
        ws = Library(self.lib_root).all()[0]
        cfg = {**self.cfg, "obsidian": {**self.cfg["obsidian"], "classify": True}}
        old = Path(obsidian.sync_paper(ws, self.cfg)["file"])  # 不开分类：先落默认文件夹
        self.assertTrue(old.exists())
        orig_cfg, orig_run = cm.engine_cfg, eng.run
        calls = []
        cm.engine_cfg = lambda cfg, mid: ({}, {})
        eng.run = lambda *a, **k: (calls.append(1), "11_生成")[1]
        try:
            r = obsidian.sync_paper(ws, cfg, classify=True)
            self.assertEqual(r["status"], "written")
            self.assertEqual(Path(r["file"]).parent, self.vault / "11_生成")
            self.assertFalse(old.exists())  # 默认文件夹里的旧文件清掉了
            self.assertEqual(len(calls), 1)
            self.assertEqual(obsidian.sync_paper(ws, cfg, classify=True)["status"], "unchanged")
            self.assertEqual(len(calls), 1)  # 第二次用缓存，模型不重复调
        finally:
            cm.engine_cfg, eng.run = orig_cfg, orig_run

    def test_sync_classify_bad_answer_falls_back(self):
        import easyread.chat_models as cm
        import easyread.engines as eng
        self.paper()
        (self.vault / "10_重建").mkdir()
        ws = Library(self.lib_root).all()[0]
        cfg = {**self.cfg, "obsidian": {**self.cfg["obsidian"], "classify": True}}
        orig_cfg, orig_run = cm.engine_cfg, eng.run
        cm.engine_cfg = lambda cfg, mid: ({}, {})
        eng.run = lambda *a, **k: "不知道该放哪"
        try:
            r = obsidian.sync_paper(ws, cfg, classify=True)
            self.assertEqual(Path(r["file"]).parent, self.vault / "论文")  # 落到默认文件夹
            self.assertIsNone(obsidian.cached_folder(ws, ["10_重建"]))  # 没缓存，下次再试
        finally:
            cm.engine_cfg, eng.run = orig_cfg, orig_run


    def test_owned_by_detects_legacy_layout(self):
        # 旧版 frontmatter 的 easyread_id 排在长作者列表后面，也要能认出来
        p = self.vault / "旧笔记.md"
        p.write_text('---\ntitle: "x"\nauthors: [' + ", ".join('"作者%d"' % i for i in range(30)) +
                     "]\neasyread_id: abcd11112222\n---\n\n正文", encoding="utf-8")
        self.assertTrue(obsidian._owned_by(p, "abcd11112222"))

    def test_classify_falls_back_to_translation_engine(self):
        import easyread.chat_models as cm
        import easyread.engines as eng
        self.paper()
        ws = Library(self.lib_root).all()[0]
        cfg = {**self.cfg, "engine": "openai", "chat": {"default": "broken", "models": []}}
        orig_cfg, orig_run = cm.engine_cfg, eng.run
        calls = []

        def fake_run(engine_cfg, prompt, cwd, images, cancel):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("403")
            return "放 10_重建"

        eng.run = fake_run
        cm.engine_cfg = lambda cfg, mid: (cfg, {})  # 第一个尝试就是这份 cfg
        try:
            self.assertEqual(obsidian.classify_paper(ws, cfg, ["10_重建", "11_生成"]), "10_重建")
            self.assertEqual(len(calls), 2)  # 第一次失败，兜底成功
        finally:
            cm.engine_cfg, eng.run = orig_cfg, orig_run


if __name__ == "__main__":
    unittest.main()

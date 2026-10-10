import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from easyread import wiki_ingest
from easyread.library import Library
from easyread.server import Handler
from easyread.store import write_json_atomic


class WikiIngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lib_root = self.tmp / "library"
        self.vault = self.tmp / "wiki-vault"
        (self.vault / "automation" / "paper-ingest").mkdir(parents=True)
        self.script = self.vault / "automation" / "paper-ingest" / "workflow.ps1"
        self.script.write_text("# workflow", encoding="utf-8")
        self.cfg = {"library_dir": str(self.lib_root), "wiki": {"vault": str(self.vault)}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def paper(self, pid="abcd11112222"):
        root = self.lib_root / pid
        root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(root / "paper.json", {"meta": {"title_en": "T"}, "blocks": []})
        (root / "deepread.md").write_text("# 精读\n\n内容", encoding="utf-8")
        (root / "source.pdf").write_bytes(b"%PDF-1.4 fake")
        return root

    def test_build_command(self):
        cmd = wiki_ingest._build_command(self.script, self.vault, Path("doc.md"), Path("paper.pdf"), "easyread-x-1")
        self.assertEqual(cmd[cmd.index("-Workflow") + 1], "ingestion")
        self.assertEqual(cmd[cmd.index("-RunId") + 1], "easyread-x-1")
        self.assertEqual(cmd[cmd.index("-VaultRoot") + 1], str(self.vault))
        self.assertIn("-AllowModelTransfer", cmd)
        self.assertTrue(str(self.script) in cmd)

    def test_parse_result(self):
        out = 'uv noise\n{\n  "status": "published",\n  "errors": []\n}\n'
        self.assertEqual(wiki_ingest._parse_result(out)["status"], "published")
        self.assertIsNone(wiki_ingest._parse_result("no json at all"))
        self.assertIsNone(wiki_ingest._parse_result('{"no_status": 1}'))
        self.assertIsNone(wiki_ingest._parse_result(""))

    def test_result_message(self):
        msg = wiki_ingest._result_message({"status": "published", "result": {"outcome": "needs_review", "note_path": "待归档/x.md"}})
        self.assertIn("待归档", msg)
        msg2 = wiki_ingest._result_message({"status": "blocked", "errors": ["hash mismatch"]})
        self.assertIn("检查未通过", msg2)
        self.assertIn("hash mismatch", msg2)

    def test_start_validations(self):
        root = self.paper()
        ws = Library(self.lib_root).all()[0]
        with self.assertRaises(ValueError):  # 没填知识库
            wiki_ingest.start(ws, {"wiki": {"vault": ""}})
        with self.assertRaises(ValueError):  # 目录不存在
            wiki_ingest.start(ws, {"wiki": {"vault": str(self.tmp / "nope")}})
        self.script.unlink()
        with self.assertRaises(ValueError):  # 目录里没有入库流程
            wiki_ingest.start(ws, self.cfg)
        self.script.write_text("# workflow", encoding="utf-8")
        (root / "deepread.md").unlink()
        with self.assertRaises(ValueError):  # 没有精读文档
            wiki_ingest.start(ws, self.cfg)
        (root / "deepread.md").write_text("# 精读", encoding="utf-8")
        (root / "source.pdf").unlink()
        with self.assertRaises(ValueError):  # 没有原 PDF
            wiki_ingest.start(ws, self.cfg)

    def test_run_finishes_published(self):
        self.paper()
        ws = Library(self.lib_root).all()[0]
        wiki_ingest._set_state(ws, state="running", run_id="r1", run_dir=str(self.tmp / "r1"))
        fake = '{\n  "status": "published",\n  "result": {"outcome": "verified", "note_path": "note.md"}\n}\n'
        orig = wiki_ingest._execute
        wiki_ingest._execute = lambda ws, cmd, log_path: (0, fake)
        try:
            wiki_ingest._run(ws, ["fake"], "r1", str(self.tmp / "r1"))
        finally:
            wiki_ingest._execute = orig
        st = wiki_ingest.read(ws)
        self.assertEqual(st["state"], "done")
        self.assertIn("已发布", st["message"])
        self.assertEqual(st["result"]["status"], "published")

    def test_run_without_result_marks_error(self):
        self.paper()
        ws = Library(self.lib_root).all()[0]
        wiki_ingest._set_state(ws, state="running", run_id="r2", run_dir=str(self.tmp / "r2"))
        orig = wiki_ingest._execute
        wiki_ingest._execute = lambda ws, cmd, log_path: (1, "boom")
        try:
            wiki_ingest._run(ws, ["fake"], "r2", str(self.tmp / "r2"))
        finally:
            wiki_ingest._execute = orig
        st = wiki_ingest.read(ws)
        self.assertEqual(st["state"], "error")
        self.assertIn("异常退出", st["message"])

    def test_read_adopts_finished_result_after_restart(self):
        self.paper()
        ws = Library(self.lib_root).all()[0]
        run_dir = self.vault / "tmp" / "paper-ingest" / "easyread-x"
        run_dir.mkdir(parents=True)
        (run_dir / "workflow-result.json").write_text(
            json.dumps({"status": "published", "result": {"outcome": "verified", "note_path": "n.md"}}), encoding="utf-8")
        wiki_ingest._set_state(ws, state="running", run_dir=str(run_dir), pid=None)
        st = wiki_ingest.read(ws)
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["result"]["status"], "published")

    def test_read_marks_dead_process_error(self):
        self.paper()
        ws = Library(self.lib_root).all()[0]
        orig = wiki_ingest._pid_alive
        wiki_ingest._pid_alive = lambda pid: False  # 进程退出后没有留下结果
        try:
            wiki_ingest._set_state(ws, state="running", run_dir=str(self.tmp / "none"), pid=12345)
            st = wiki_ingest.read(ws)
        finally:
            wiki_ingest._pid_alive = orig
        self.assertEqual(st["state"], "error")
        self.assertIn("中断", st["message"])

    def test_pid_alive(self):
        self.assertTrue(wiki_ingest._pid_alive(os.getpid()))
        self.assertFalse(wiki_ingest._pid_alive(None))
        self.assertFalse(wiki_ingest._pid_alive(0))
        self.assertFalse(wiki_ingest._pid_alive("abc"))

    def test_cancel_without_run(self):
        self.paper()
        ws = Library(self.lib_root).all()[0]
        self.assertFalse(wiki_ingest.cancel(ws)["ok"])


class WikiIngestServerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        write_json_atomic(self.root / "paper.json", {"meta": {"title_en": "Test"}, "blocks": []})
        (self.root / "deepread.md").write_text("文档", encoding="utf-8")
        self.lib = Library(self.root.parent)
        Handler.app = SimpleNamespace(lib=self.lib, jobs=SimpleNamespace(submit_deepread=lambda ws, cfg=None: {}), token="test-token")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.orig_start = wiki_ingest.start

    def tearDown(self):
        wiki_ingest.start = self.orig_start
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_get_and_post(self):
        pid = self.root.name
        with urllib.request.urlopen(self.base + f"/api/p/{pid}/wikiingest") as response:
            data = json.loads(response.read())
        self.assertEqual(data["state"], "idle")
        self.assertEqual(data["log"], "")

        wiki_ingest.start = lambda ws, cfg: {"state": "running", "message": "知识库入库中"}
        body = json.dumps({}).encode()
        req = urllib.request.Request(self.base + f"/api/p/{pid}/wikiingest", data=body,
                                     headers={"Content-Type": "application/json", "X-Token": "test-token"}, method="POST")
        with urllib.request.urlopen(req) as response:
            self.assertEqual(json.loads(response.read())["state"], "running")

        bad = urllib.request.Request(self.base + f"/api/p/{pid}/wikiingest", data=body, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad)
        self.assertEqual(err.exception.code, 403)


if __name__ == "__main__":
    unittest.main()

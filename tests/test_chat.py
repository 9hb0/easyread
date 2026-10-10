"""“问 AI”的流式输出：用一个本机假的 OpenAI 兼容接口测，包括推理模型的 <think> 被去掉。  python -m unittest tests.test_chat"""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from easyread import chat, chat_models
from easyread.store import Workspace, write_json_atomic

PIECES = ["<think>先想", "一想</think>", "标准误差", r"除以 $\sqrt{n}$", "。"]


class FakeAPI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not hasattr(self.server, "bodies"):
            self.server.bodies = []
        self.server.bodies.append(body)
        assert body["stream"] is True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for p in PIECES:
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": p}}]}).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


class ChatContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = Workspace(self.root)
        self.blocks = [
            {"id": f"p{n}", "type": "para", "page": n, "en": f"Original section {n}.", "zh": f"第 {n} 页译文"}
            for n in range(1, 11)
        ]
        self.paper = {"meta": {"title_en": "Context test", "page_count": 10}, "blocks": self.blocks,
                      "references": [{"id": "75", "text": "Smith. A cited method. https://example.org/method"}]}
        self.save_paper()

    def save_paper(self):
        write_json_atomic(self.root / "paper.json", self.paper)

    def ask(self, engine="openai", anchor="p5"):
        return chat.prompt(self.ws, [{"role": "user", "content": "参考文献 [75] 讲了什么？"}], anchor, "", engine)

    def test_other_sections_and_reference_entries_reach_every_engine(self):
        for engine in ("openai", "claude", "codex"):
            for anchor in ("p5", None):
                with self.subTest(engine=engine, anchor=anchor):
                    text = self.ask(engine, anchor)
                    self.assertIn("Original section 1.", text)
                    self.assertIn("Original section 10.", text)
                    self.assertIn("[75] Smith. A cited method. https://example.org/method", text)

    def test_untranslated_text_including_references_on_a_translated_page(self):
        self.paper["blocks"] = self.blocks[:5]
        self.paper["references"] = []
        self.save_paper()
        extract = self.root / "extract"
        extract.mkdir()
        (extract / "page-005.txt").write_text("Section 5. References\n[80] Jones. Untranslated citation.", encoding="utf-8")
        (extract / "page-010.txt").write_text("Appendix proof on the last page.", encoding="utf-8")
        text = self.ask()
        self.assertIn("[80] Jones. Untranslated citation.", text)
        self.assertIn("Appendix proof on the last page.", text)
        self.assertIn("Original section 1.", text)
        self.assertIn("第 10 页", text)

    def test_block_fallback_preserves_lists_math_and_table_values(self):
        self.paper["blocks"] += [
            {"id": "l10", "type": "list", "page": 10, "items": [{"en": "An English-only list item."}]},
            {"id": "eq10", "type": "math", "page": 10, "tex": "E=mc^2", "tag": "8"},
            {"id": "t10", "type": "table", "page": 10, "caption_en": "Full results", "head": [["Method", "Score"]], "rows": [["Baseline", "73.5"]]},
            {"id": "f10", "type": "figure", "page": 10, "caption_en": "Overview of the method."},
            {"id": "zh10", "type": "para", "page": 10, "zh": "只有译文的附录内容"},
        ]
        self.save_paper()
        text = self.ask()
        for value in ("An English-only list item.", "E=mc^2", "Full results", "Baseline", "73.5", "Overview of the method.", "只有译文的附录内容"):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_empty_extracted_page_falls_back_to_blocks(self):
        extract = self.root / "extract"
        extract.mkdir()
        (extract / "page-010.txt").write_text(" \n", encoding="utf-8")
        self.assertIn("Original section 10.", self.ask())

    def test_partial_extracted_page_keeps_saved_body_math_and_table(self):
        self.paper["blocks"] += [
            {"id": "eq10", "type": "math", "page": 10, "tex": "E=mc^2"},
            {"id": "t10", "type": "table", "page": 10, "rows": [["Baseline", "73.5"]]},
        ]
        self.save_paper()
        extract = self.root / "extract"
        extract.mkdir()
        (extract / "page-010.txt").write_text("Conference header only.", encoding="utf-8")
        text = self.ask(anchor="p1")
        for value in ("Conference header only.", "Original section 10.", "E=mc^2", "Baseline", "73.5"):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_inline_reading_notes_keep_their_non_paper_identity(self):
        self.paper["blocks"].append({"id": "note10", "type": "note", "page": 10, "zh": "这是阅读者自己的推测。"})
        self.save_paper()
        text = self.ask(anchor="p1")
        self.assertIn("阅读批注（非原文）：这是阅读者自己的推测。", text)

    def test_chat_http_request_sends_other_pages_and_references_to_api(self):
        import urllib.request
        from types import SimpleNamespace
        from unittest.mock import patch
        from easyread.library import Library
        from easyread.server import Handler

        extract = self.root / "extract"
        extract.mkdir()
        (extract / "page-010.txt").write_text("Appendix proof.\n[80] Jones. Another cited method.", encoding="utf-8")
        api = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=api.serve_forever, daemon=True).start()
        self.addCleanup(api.server_close)
        self.addCleanup(api.shutdown)
        cfg = {"openai": {}, "chat": {"default": "fake", "models": [{
            "id": "fake", "name": "Test API", "engine": "openai", "model": "fake",
            "base_url": f"http://127.0.0.1:{api.server_address[1]}/v1",
        }]}}
        app = SimpleNamespace(lib=Library(self.root.parent), token="test-token")
        with patch.object(Handler, "app", app, create=True), patch("easyread.server.config.load", return_value=cfg):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/p/{self.root.name}/chat",
                    data=json.dumps({"text": "Explain citations [75] and [80].", "anchor": "p5"}).encode(),
                    headers={"Content-Type": "application/json", "X-Token": "test-token"}, method="POST")
                with urllib.request.urlopen(request, timeout=5) as response:
                    events = [json.loads(line) for line in response]
            finally:
                server.shutdown()
                server.server_close()
        self.assertTrue(events[-1].get("done"), events)
        text = api.bodies[0]["messages"][0]["content"]
        for value in ("Original section 1.", "Original section 5.", "Appendix proof.",
                      "[75] Smith. A cited method. https://example.org/method", "[80] Jones. Another cited method."):
            with self.subTest(value=value):
                self.assertIn(value, text)


class ChatStreamTest(unittest.TestCase):
    def test_openai_stream_strips_think(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cfg = {"engine": "openai", "openai": {"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "fake", "api_key": "k"}}
            out = "".join(chat.stream(cfg, "问题", None, threading.Event()))
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(out, r"标准误差除以 $\sqrt{n}$。")

    def test_model_list_picks_preset_key(self):
        cfg = {"engine": "claude", "claude": {"model": ""}, "codex": {"model": ""},
               "chat": {"default": "ds", "models": [{"id": "ds", "name": "DeepSeek", "engine": "openai", "preset": "deepseek", "model": ""}]},
               "openai": {"preset": "zhipu", "base_url": "x", "model": "glm", "api_key": "zk", "keys": {"zhipu": "zk", "deepseek": "dk"}}}
        e, m = chat_models.engine_cfg(cfg, None)
        self.assertEqual((e["engine"], e["openai"]["api_key"], e["openai"]["model"], m["id"]), ("openai", "dk", "deepseek-chat", "ds"))

    def test_engine_cfg_passes_vision_per_model(self):
        cfg = {"engine": "claude", "claude": {}, "codex": {},
               "chat": {"default": "v", "models": [{"id": "v", "engine": "openai", "preset": "minimax", "model": "MiniMax-M3", "vision": True},
                                                     {"id": "t", "engine": "openai", "preset": "deepseek", "model": "deepseek-chat"}]},
               "openai": {"preset": "", "base_url": "", "model": "", "api_key": "", "keys": {}}}
        self.assertTrue(chat_models.engine_cfg(cfg, "v")[0]["openai"]["vision"])
        self.assertFalse(chat_models.engine_cfg(cfg, "t")[0]["openai"]["vision"])

    def test_sanitize_keeps_vision(self):
        out = chat_models.sanitize([{"engine": "openai", "preset": "minimax", "model": "MiniMax-M3", "vision": True},
                                    {"engine": "openai", "preset": "deepseek", "model": "deepseek-chat"}])
        self.assertTrue(out[0]["vision"])
        self.assertFalse(out[1]["vision"])

    def test_vision_pages_from_anchor_and_refs(self):
        import shutil, tempfile
        from pathlib import Path
        from easyread.store import Workspace, write_json_atomic
        root = Path(tempfile.mkdtemp())
        try:
            write_json_atomic(root / "paper.json", {"blocks": [{"id": "a", "page": 1}, {"id": "b", "page": 3}, {"id": "c", "page": 5}, {"id": "d", "page": 7}]})
            ws = Workspace(root)
            self.assertEqual(chat.vision_pages(ws, "b", [{"anchor": "c"}]), [3, 5])
            self.assertEqual(chat.vision_pages(ws, None, []), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_vision_model_sends_page_images(self):
        import base64, shutil, tempfile
        from pathlib import Path
        imgdir = Path(tempfile.mkdtemp())
        img = imgdir / "page-001.jpg"
        img.write_bytes(b"\xff\xd8fakejpg")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cfg = {"engine": "openai", "openai": {"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "fake", "api_key": "k", "vision": True}}
            out = "".join(chat.stream(cfg, "问题", None, threading.Event(), None, [img]))
            body = srv.bodies[-1]
        finally:
            srv.shutdown()
            srv.server_close()
            shutil.rmtree(imgdir, ignore_errors=True)
        self.assertEqual(out, r"标准误差除以 $\sqrt{n}$。")
        content = body["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "问题"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8fakejpg").decode())

    def test_text_model_gets_no_images(self):
        import shutil, tempfile
        from pathlib import Path
        imgdir = Path(tempfile.mkdtemp())
        (imgdir / "page-001.jpg").write_bytes(b"jpg")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cfg = {"engine": "openai", "openai": {"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "fake", "api_key": "k", "vision": False}}
            "".join(chat.stream(cfg, "问题", None, threading.Event(), None, [imgdir / "page-001.jpg"]))
            body = srv.bodies[-1]
        finally:
            srv.shutdown()
            srv.server_close()
            shutil.rmtree(imgdir, ignore_errors=True)
        self.assertEqual(body["messages"][0]["content"], "问题")

    def test_threads_and_legacy(self):
        import shutil, tempfile
        from pathlib import Path
        from easyread import chat_store
        from easyread.store import Workspace, write_json_atomic
        root = Path(tempfile.mkdtemp())
        try:
            write_json_atomic(root / "paper.json", {"blocks": []})
            write_json_atomic(root / "chat.json", {"messages": [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]})
            ws = Workspace(root)
            chat_store.append(ws, "t2", {"content": "新问题"}, "新回答", "opus", "Claude Opus 5.5")
            ts = chat_store.threads(ws)
            self.assertEqual([t["title"] for t in ts], ["新问题", "旧问题"])
            chat_store.delete(ws, "t-first")
            self.assertEqual(len(chat_store.threads(ws)), 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_marks_only_when_asked_and_refs(self):
        import shutil, tempfile
        from pathlib import Path
        from easyread.store import Workspace, write_json_atomic
        root = Path(tempfile.mkdtemp())
        try:
            blocks = [{"id": "a", "type": "p", "zh": "甲段 $x$"}, {"id": "b", "type": "p", "zh": "乙段"}, {"id": "c", "type": "p", "zh": "丙段"}]
            write_json_atomic(root / "paper.json", {"meta": {}, "blocks": blocks})
            write_json_atomic(root / "reader.json", {"notes": {"n1": {"anchor": "a", "quote": "红色重点", "color": "pink", "kind": "highlight"},
                                                                "n2": {"anchor": "b", "quote": "黄色句子", "color": "yellow", "kind": "highlight"}}})
            ws = Workspace(root)
            refs = [{"anchor": "b", "quote": ""}, {"anchor": "c", "quote": "丙"}]
            plain = chat.prompt(ws, [{"role": "user", "content": "这两段什么关系"}], "b", "", "openai", refs)
            self.assertNotIn("红色重点", plain)           # 没问到标记，就不带
            self.assertIn("2 处标记", plain)
            self.assertIn("[c] 读者选中：「丙」", plain)   # 引用的第二段也在
            red = chat.prompt(ws, [{"role": "user", "content": "我标红的那些有什么联系"}], None, "", "openai")
            self.assertIn("红色重点", red)
            self.assertNotIn("黄色句子", red)              # 只带问到的颜色
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

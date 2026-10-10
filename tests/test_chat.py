"""“问 AI”的流式输出：用一个本机假的 OpenAI 兼容接口测，包括推理模型的 <think> 被去掉。  python -m unittest tests.test_chat"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from easyread import chat, chat_models

PIECES = ["<think>先想", "一想</think>", "标准误差", "除以 $\sqrt{n}$", "。"]


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


class ChatStreamTest(unittest.TestCase):
    def test_openai_stream_strips_think(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cfg = {"engine": "openai", "openai": {"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "fake", "api_key": "k"}}
            out = "".join(chat.stream(cfg, "问题", None, threading.Event()))
        finally:
            srv.shutdown()
        self.assertEqual(out, "标准误差除以 $\sqrt{n}$。")

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
            shutil.rmtree(imgdir, ignore_errors=True)
        self.assertEqual(out, "标准误差除以 $\sqrt{n}$。")
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

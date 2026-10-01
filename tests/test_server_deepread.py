import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer

from easyread.library import Library
from easyread.server import Handler
from easyread.store import write_json_atomic


class FakeJobs:
    def __init__(self):
        self.calls = 0

    def submit_deepread(self, ws, cfg=None):
        self.calls += 1
        return {"state": "queued", "message": "排队中", "content": "", "has_document": False}


class DeepReadServerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        write_json_atomic(self.root / "paper.json", {"meta": {"title_en": "Test"}, "blocks": []})
        write_json_atomic(self.root / "deepread.json", {"state": "done", "chars": 3})
        (self.root / "deepread.md").write_text("旧文档", encoding="utf-8")
        self.lib = Library(self.root.parent)
        self.fake_jobs = FakeJobs()
        Handler.app = SimpleNamespace(lib=self.lib, jobs=self.fake_jobs, token="test-token")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_get_returns_document_and_post_requires_token(self):
        pid = self.root.name
        with urllib.request.urlopen(self.base + f"/api/p/{pid}/deepread") as response:
            data = json.loads(response.read())
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["content"], "旧文档")

        body = json.dumps({}).encode()
        req = urllib.request.Request(self.base + f"/api/p/{pid}/deepread", data=body, headers={"Content-Type": "application/json", "X-Token": "test-token"}, method="POST")
        with urllib.request.urlopen(req) as response:
            self.assertEqual(json.loads(response.read())["state"], "queued")
        self.assertEqual(self.fake_jobs.calls, 1)

        bad = urllib.request.Request(self.base + f"/api/p/{pid}/deepread", data=body, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad)
        self.assertEqual(err.exception.code, 403)


if __name__ == "__main__":
    unittest.main()

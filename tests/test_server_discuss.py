"""页边 AI 讨论的删除接口：删掉 discussion.json 里的一条，不影响其他数据。"""
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from easyread.library import Library
from easyread.server import Handler
from easyread.store import write_json_atomic


class FakeJobs:
    pass


class DiscussDeleteTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        write_json_atomic(self.root / "paper.json", {"meta": {"title_en": "Test"}, "blocks": []})
        write_json_atomic(self.root / "discussion.json", {"schema": 2, "entries": [
            {"id": "d001-aa", "kind": "qa", "q": "问题", "body": "回答", "at": "2026-01-01T00:00:00"},
            {"id": "d002-bb", "kind": "explain", "body": "解释", "at": "2026-01-01T00:00:01"},
        ]})
        self.lib = Library(self.root.parent)
        Handler.app = SimpleNamespace(lib=self.lib, jobs=FakeJobs(), token="test-token")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def post(self, body, token="test-token"):
        req = urllib.request.Request(
            self.base + f"/api/p/{self.root.name}/discuss/delete",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **({"X-Token": token} if token else {})},
            method="POST")
        return urllib.request.urlopen(req)

    def test_delete_removes_entry_and_keeps_rest(self):
        with self.post({"id": "d001-aa"}) as r:
            data = json.loads(r.read())
        self.assertEqual(data["deleted"], 1)
        disc = json.loads((self.root / "discussion.json").read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for e in disc["entries"]], ["d002-bb"])

    def test_unknown_id_deletes_nothing(self):
        with self.post({"id": "nope"}) as r:
            self.assertEqual(json.loads(r.read())["deleted"], 0)

    def test_requires_token_and_id(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post({"id": "d001-aa"}, token="wrong")
        self.assertEqual(err.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post({})
        self.assertEqual(err.exception.code, 400)


if __name__ == "__main__":
    unittest.main()


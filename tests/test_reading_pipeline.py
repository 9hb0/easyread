"""A real two-page PDF through prepare, durable jobs and the reader HTTP API."""
import copy
import json
import queue
import shutil
import threading
import urllib.request
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

from easyread import jobs, sources, translate
from easyread.library import Library
from easyread.server import Handler
from tests.test_translate import make_ws


def tiny_pdf():
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>"]
    for page, text in ((1, b"We propose a small model."), (2, b"Accuracy rises by 2 percent.")):
        content_id = 4 if page == 1 else 6
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 7 0 R >> >> /Contents {content_id} 0 R >>".encode())
        stream = b"BT /F1 12 Tf 50 700 Td (" + text + b") Tj ET"
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    data, positions = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        positions.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 8\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in positions[1:])
    return data + f"trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


class ReadingPipelineTest(unittest.TestCase):
    def setUp(self):
        self.ws = make_ws(2)
        self.addCleanup(shutil.rmtree, self.ws.root, True)
        (self.ws.root / "source.pdf").write_bytes(tiny_pdf())
        self.ws.update("paper", lambda p: p["meta"].update(pages=[], page_count=0))
        self.jobs = object.__new__(jobs.Jobs)
        self.jobs.bulk, self.jobs.small, self.jobs.cancels = queue.Queue(), queue.Queue(), {}
        self.jobs.lock, self.jobs.recent = threading.Lock(), []
        self.lib = Library(self.ws.root.parent)
        self.jobs.lib = self.lib
        self.cfg = {"engine": "openai", "openai": {"vision": False}, "reading_enabled": True,
                    "batch_pages": 2, "concurrency": 1}

    def test_pdf_job_paper_and_read_api(self):
        response = {"blocks": [
            {"id": "p1-1", "type": "para", "page": 1, "en": "We propose a small model.", "zh": "我们提出小模型。",
             "highlights": [{"block_id": "p1-1", "category": "method", "quote": "小模型", "occurrence": 0}],
             "annotations": [{"type": "method_explanation", "anchor_quote": "小模型", "content": "这是本文提出的方法。",
                               "basis": "author_statement", "source_refs": [{"page": 1, "block": "p1-1", "quote": "We propose a small model."}]}]},
            {"id": "p2-1", "type": "para", "page": 2, "en": "Accuracy rises by 2 percent.", "zh": "准确率提升2%。"}],
            "insights": [{"type": "method", "block": "p1-1", "content": "提出小模型。", "status": "provisional",
                          "source_refs": [{"page": 1, "block": "p1-1", "quote": "We propose a small model."}]}]}
        synthesis = {"summary": "论文提出小模型。", "summary_refs": response["insights"][0]["source_refs"], "checks": [], "relations": []}
        seen = []
        def engine(cfg, prompt, *a):
            seen.append(prompt)
            return json.dumps(synthesis if "请整合成研究导航" in prompt else response)
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(sources, "enrich", return_value={}), \
             mock.patch.object(translate.engines, "run", side_effect=engine):
            self.jobs.enqueue(self.ws)
            self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())
        self.assertEqual(self.ws.load("job")["state"], "done")
        self.assertEqual(len(seen), 1)  # translation alone never invokes reading or synthesis
        paper = self.ws.load("paper")
        self.assertEqual(paper["translation"]["done_pages"], [1, 2])
        self.assertTrue((self.ws.root / "pages" / "page-001.webp").is_file())
        for block in paper["blocks"]:
            self.assertNotIn("highlights", block)
            self.assertNotIn("annotations", block)
        self.assertEqual(paper["reading"].get("insights", []), [])
        self.assertEqual(paper["reading_guide"]["coverage"]["analyzed"], [])
        self.assertFalse(paper["reading_guide"]["coverage"]["full_complete"])
        self.assertFalse(paper["reading_guide"].get("synth_of"))
        self.assertEqual([paper["reading"]["pages"][str(p)]["status"] for p in (1, 2)], ["pending", "pending"])
        translated_blocks = copy.deepcopy(paper["blocks"])
        app = mock.patch.object(Handler, "app", SimpleNamespace(lib=self.lib, jobs=self.jobs, token="fixture-token"), create=True)
        app.start()
        self.addCleanup(app.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}/api/p/{self.ws.id}"
        with urllib.request.urlopen(base + "/part/paper") as fetched:
            loaded = json.loads(fetched.read())["data"]
        self.assertEqual(loaded, paper)
        request = urllib.request.Request(base + "/enrich", data=b'{}', headers={"X-Token": "fixture-token"})
        with urllib.request.urlopen(request) as fetched:
            queued = json.loads(fetched.read())
        self.assertEqual(queued["type"], "enrich")
        self.assertEqual(queued["pages"], [1, 2])
        self.assertEqual(queued["state"], "queued")
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate.engines, "run", side_effect=engine):
            self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())
        self.assertEqual(self.ws.load("job")["state"], "done")
        self.assertEqual(len(seen), 3)  # translate, explicit enrich, condensed synthesis
        enriched = self.ws.load("paper")
        self.assertEqual([{k: v for k, v in b.items() if k not in ("highlights", "annotations")}
                          for b in enriched["blocks"]], translated_blocks)
        self.assertTrue(enriched["reading_guide"]["coverage"]["full_complete"])
        self.assertTrue(enriched["reading_guide"]["synth_of"])
        self.assertEqual(enriched["blocks"][0]["highlights"][0]["quote"], "小模型")
        self.assertEqual(enriched["blocks"][0]["annotations"][0]["source_refs"], synthesis["summary_refs"])
        with urllib.request.urlopen(base + "/part/paper") as fetched:
            self.assertEqual(json.loads(fetched.read())["data"], enriched)
        request = urllib.request.Request(base + "/enrich", data=b'{"pages":[1]}', headers={"X-Token": "fixture-token"})
        with urllib.request.urlopen(request) as fetched:
            self.assertEqual(json.loads(fetched.read())["type"], "enrich")
        self.assertEqual(self.ws.load("job")["pages"], [1])


if __name__ == "__main__":
    unittest.main()

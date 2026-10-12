import queue
import shutil
import threading
import unittest
from unittest import mock

from easyread import jobs, reading, translate
from tests.test_translate import make_ws


class ReadingJobsTest(unittest.TestCase):
    def setUp(self):
        self.ws = make_ws(4)
        self.addCleanup(shutil.rmtree, self.ws.root, True)
        self.jobs = object.__new__(jobs.Jobs)
        self.jobs.bulk, self.jobs.small = queue.Queue(), queue.Queue()
        self.jobs.lock = threading.Lock()
        self.jobs.recent, self.jobs.cancels = [], {}
        self.jobs.lib = mock.Mock()
        self.jobs.lib.all.return_value = [self.ws]
        self.cfg = {"engine": "openai", "reading_enabled": True}

    def test_enrich_is_persisted_and_resumed(self):
        self.jobs.enqueue_enrich(self.ws, [2, 3])
        self.assertEqual(self.ws.load("job")["type"], "enrich")
        self.ws.update("job", lambda j: j.update(state="running"))
        self.jobs.bulk = queue.Queue()
        self.jobs._resume()
        self.assertEqual(self.jobs.bulk.get_nowait(), self.ws.id)
        self.assertEqual(self.ws.load("job")["pages"], [2, 3])

    def test_enrich_failure_is_not_reported_done(self):
        self.jobs.enqueue_enrich(self.ws, [1, 2])
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "enrich_pages", return_value={2: "bad quote"}):
            self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())
        self.assertEqual(self.ws.load("job")["state"], "partial")
        self.assertEqual(self.ws.load("job")["failed"], {"2": "bad quote"})

    def test_cancelled_synthesis_propagates(self):
        self.jobs.enqueue(self.ws)
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "translate_pages", return_value={}), \
             mock.patch.object(translate, "finalize_reading", side_effect=jobs.Cancelled()):
            with self.assertRaises(jobs.Cancelled):
                self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())

    def test_restart_skips_pages_committed_by_same_task(self):
        self.jobs.enqueue(self.ws, [1, 2])
        job = self.ws.load("job")
        self.ws.update("paper", lambda p: p["translation"].update(
            done_pages=[1], completed_revisions={"1": f"{job['task_id']}:1"}))
        seen = []
        def run(ws, cfg, pages, cancel, report):
            seen.extend(pages)
            return {}
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "translate_pages", side_effect=run), \
             mock.patch.object(translate, "finalize_reading"):
            self.jobs._run_bulk(self.ws, job, threading.Event())
        self.assertEqual(seen, [2])


if __name__ == "__main__":
    unittest.main()

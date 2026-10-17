import queue
import shutil
import threading
import unittest
from unittest import mock

from easyread import jobs, reading, translate
from easyread.paperdata import merge_blocks
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

    def seed_translated_pages(self):
        merge_blocks(self.ws, {"blocks": [
            {"id": f"p{p}-1", "type": "para", "page": p,
             "en": f"Text of page {p}.", "zh": f"第{p}页译文。"} for p in (1, 2, 3, 4)]}, done=[1, 2, 3, 4])
        self.ws.update("paper", lambda p: reading.apply_reading_state(
            p, [1, 2, 3, 4], {1: "completed", 2: "failed", 3: "stale", 4: "skipped"}, True))

    def test_enrich_is_persisted_and_resumed(self):
        self.jobs.enqueue_enrich(self.ws, [2, 3])
        self.assertEqual(self.ws.load("job")["type"], "enrich")
        self.ws.update("job", lambda j: j.update(state="running"))
        self.jobs.bulk = queue.Queue()
        self.jobs._resume()
        self.assertEqual(self.jobs.bulk.get_nowait(), self.ws.id)
        self.assertEqual(self.ws.load("job")["pages"], [2, 3])
        self.assertTrue(self.ws.load("job")["resume"])

    def test_enrich_failure_is_not_reported_done(self):
        self.jobs.enqueue_enrich(self.ws, [1, 2])
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "enrich_pages", return_value={2: "bad quote"}):
            self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())
        self.assertEqual(self.ws.load("job")["state"], "partial")
        self.assertEqual(self.ws.load("job")["failed"], {"2": "bad quote"})

    def test_translate_job_does_not_start_reading_or_synthesis(self):
        self.jobs.enqueue(self.ws)
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "translate_pages", return_value={}) as run, \
             mock.patch.object(translate, "enrich_pages") as enrich, \
             mock.patch.object(translate, "finalize_reading", side_effect=jobs.Cancelled()) as finalize:
            self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())
        self.assertEqual(run.call_args.args[2], [1, 2, 3, 4])
        enrich.assert_not_called()
        finalize.assert_not_called()
        self.assertEqual(self.ws.load("job")["state"], "done")

    def test_completed_translate_job_does_not_start_synthesis(self):
        self.seed_translated_pages()
        self.jobs.enqueue(self.ws)
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "translate_pages") as run, \
             mock.patch.object(translate, "enrich_pages") as enrich, \
             mock.patch.object(translate, "finalize_reading") as finalize:
            self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())
        run.assert_not_called()
        enrich.assert_not_called()
        finalize.assert_not_called()
        self.assertEqual(self.ws.load("job")["state"], "done")

    def test_manual_enrich_cancellation_propagates(self):
        self.jobs.enqueue_enrich(self.ws, [1])
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "enrich_pages", side_effect=jobs.Cancelled()):
            with self.assertRaises(jobs.Cancelled):
                self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())
        self.assertNotEqual(self.ws.load("job")["state"], "done")

    def test_manual_enrich_defaults_to_all_translated_pages(self):
        self.seed_translated_pages()
        self.assertEqual(reading.pages_needing_reading(self.ws.load("paper")), [2, 3, 4])
        self.jobs.enqueue_enrich(self.ws)
        job = self.ws.load("job")
        self.assertEqual(job["pages"], [1, 2, 3, 4])
        self.assertFalse(job["resume"])
        with mock.patch.object(jobs.config, "load", return_value=dict(self.cfg, reading_enabled=False)), \
             mock.patch.object(translate, "enrich_pages", return_value={}) as run, \
             mock.patch.object(translate, "translate_pages") as translating:
            self.jobs._run_bulk(self.ws, job, threading.Event())
        self.assertEqual(run.call_args.args[2], [1, 2, 3, 4])
        self.assertFalse(run.call_args.args[1]["reading_enabled"])
        translating.assert_not_called()
        self.assertEqual(self.ws.load("job")["state"], "done")

    def test_resumed_enrich_only_retries_missing_pages_in_original_selection(self):
        self.seed_translated_pages()
        self.jobs.enqueue_enrich(self.ws, [1, 2, 3])
        self.ws.update("job", lambda j: j.update(state="running"))
        self.jobs.bulk = queue.Queue()
        self.jobs._resume()
        job = self.ws.load("job")
        self.assertEqual(job["pages"], [1, 2, 3])
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "enrich_pages", return_value={}) as run:
            self.jobs._run_bulk(self.ws, job, threading.Event())
        self.assertEqual(run.call_args.args[2], [2, 3])

    def test_legacy_resumed_enrich_without_pages_filters_all_translated_pages(self):
        self.seed_translated_pages()
        self.jobs.enqueue_enrich(self.ws)
        self.ws.update("job", lambda j: j.update(pages=None, state="running"))
        self.jobs._resume()
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "enrich_pages", return_value={}) as run:
            self.jobs._run_bulk(self.ws, self.ws.load("job"), threading.Event())
        self.assertEqual(run.call_args.args[2], [2, 3, 4])

    def test_fresh_manual_enrich_resets_old_resume_filter(self):
        self.seed_translated_pages()
        self.jobs._write(self.ws, type="enrich", state="done", resume=True)
        self.jobs.enqueue_enrich(self.ws, [1])
        job = self.ws.load("job")
        self.assertFalse(job["resume"])
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "enrich_pages", return_value={}) as run:
            self.jobs._run_bulk(self.ws, job, threading.Event())
        self.assertEqual(run.call_args.args[2], [1])

    def test_enrich_rejects_active_translation_and_deduplicates_active_reading(self):
        self.jobs.enqueue(self.ws)
        translating = self.ws.load("job")
        with self.assertRaisesRegex(ValueError, "正在翻译"):
            self.jobs.enqueue_enrich(self.ws, [1])
        self.assertEqual(self.ws.load("job"), translating)
        self.jobs._write(self.ws, state="done")
        self.jobs.enqueue_enrich(self.ws, [1])
        reading_job = self.ws.load("job")
        queued_count = self.jobs.bulk.qsize()
        self.assertEqual(self.jobs.enqueue_enrich(self.ws, [2]), reading_job)
        self.assertEqual(self.jobs.bulk.qsize(), queued_count)
        self.assertEqual(self.ws.load("job"), reading_job)

    def test_old_enrich_job_cannot_overwrite_new_job_status(self):
        self.jobs.enqueue_enrich(self.ws, [1])
        original = self.ws.load("job")
        newer = {}
        def run(ws, cfg, pages, cancel, report):
            self.jobs.enqueue(ws, [2])
            newer.update(ws.load("job"))
            report(1, 1, "old reading result")
            return {1: "old reading failure"}
        with mock.patch.object(jobs.config, "load", return_value=self.cfg), \
             mock.patch.object(translate, "enrich_pages", side_effect=run):
            self.jobs._run_bulk(self.ws, original, threading.Event())
        self.assertEqual(self.ws.load("job"), newer)

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

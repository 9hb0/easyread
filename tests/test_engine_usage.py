import json
import unittest
from pathlib import Path
from unittest import mock
from easyread import engines


class EngineUsageTest(unittest.TestCase):
    def codex_reply(self, events, model="example-model"):
        def communicate(proc, *args):
            Path(proc[proc.index("-o") + 1]).write_text('{"ok": true}', encoding="utf-8")
            self.assertIn("--json", proc)
            return events
        with mock.patch.object(engines, "codex_path", return_value="codex.exe"), \
             mock.patch.object(engines, "_popen", side_effect=lambda args, cwd: args), \
             mock.patch.object(engines, "_communicate", side_effect=communicate):
            self.assertEqual(engines.run({"engine": "codex", "codex": {"model": model}},
                                        "test", Path.cwd()), '{"ok": true}')

    def test_codex_jsonl_usage_counts_each_turn_without_double_counting_cache(self):
        events = [{"type": "thread.started", "thread_id": "example"},
                  {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 20}},
                  {"type": "turn.completed", "usage": {"input_tokens": 40, "cached_input_tokens": 30, "output_tokens": 10}}]
        self.codex_reply("\n".join(json.dumps(e) for e in events))
        self.assertEqual(engines.last_usage(), {"prompt_tokens": 140, "completion_tokens": 30, "total_tokens": 170})
        self.assertEqual(engines.last_model(), "example-model")

    def test_codex_missing_or_invalid_usage_is_unknown_and_does_not_leak(self):
        self.codex_reply('{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}')
        self.codex_reply('warning\n[]\n{"type":"turn.completed","usage":{"input_tokens":null}}', model="")
        self.assertIsNone(engines.last_usage())
        self.assertIsNone(engines.last_model())

    def test_codex_one_bad_turn_voids_the_whole_total_instead_of_undercounting(self):
        good = {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}
        for bad in ('{"type":"turn.completed"}',
                    '{"type":"turn.completed","usage":{"input_tokens":100}}',
                    '{"type":"turn.completed","usage":{"input_tokens":-1,"output_tokens":5}}',
                    '{"type":"turn.completed","usage":"n/a"}'):
            with self.subTest(bad=bad):
                self.codex_reply("\n".join(json.dumps(e) if isinstance(e, dict) else e for e in (good, bad)))
                self.assertIsNone(engines.last_usage())

    def test_claude_cli_usage_includes_cached_input(self):
        reply = {"result": '{"ok": true}', "subtype": "success", "is_error": False,
                 "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 30,
                           "cache_creation_input_tokens": 40}, "modelUsage": {"claude-example": {}}}
        with mock.patch.object(engines, "claude_path", return_value="claude.exe"), \
             mock.patch.object(engines, "_popen"), \
             mock.patch.object(engines, "_communicate", return_value=json.dumps(reply)):
            self.assertEqual(engines.run({"engine": "claude", "claude": {}}, "test", Path.cwd()), '{"ok": true}')
        self.assertEqual(engines.last_usage()["prompt_tokens"], 170)
        self.assertEqual(engines.last_usage()["total_tokens"], 190)
        self.assertEqual(engines.last_model(), "claude-example")


if __name__ == "__main__":
    unittest.main()

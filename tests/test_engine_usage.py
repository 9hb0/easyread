import json
import unittest
from pathlib import Path
from unittest import mock
from easyread import engines


class EngineUsageTest(unittest.TestCase):
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

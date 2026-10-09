import unittest
from pathlib import Path

from easyread.presets import PRESETS


class PresetTest(unittest.TestCase):
    def test_volcano_ark_agent_and_coding_plans_are_paid_presets(self):
        by_id = {p["id"]: p for p in PRESETS}
        agent = by_id["ark-agent"]
        coding = by_id["ark-coding"]
        self.assertEqual(agent["group"], "paid")
        self.assertEqual(agent["base_url"], "https://ark.cn-beijing.volces.com/api/plan/v3")
        self.assertEqual(coding["group"], "paid")
        self.assertEqual(coding["base_url"], "https://ark.cn-beijing.volces.com/api/coding/v3")
        for preset in (agent, coding):
            self.assertTrue(preset["key"])
            self.assertIn(preset["model"], preset["models"])
            self.assertTrue(preset["key_url"])

    def test_paid_api_settings_include_custom_provider(self):
        settings = Path("easyread/web/js/common/settings.js").read_text(encoding="utf-8")
        self.assertIn('g === "paid"', settings)
        self.assertIn("自定义提供商", settings)

    def test_opencode_go_is_paid_preset_with_vision(self):
        by_id = {p["id"]: p for p in PRESETS}
        preset = by_id["opencode-go"]
        self.assertEqual(preset["group"], "paid")
        self.assertEqual(preset["base_url"], "https://opencode.ai/zen/go/v1")
        self.assertTrue(preset["key"])
        self.assertIn(preset["model"], preset["models"])
        settings = Path("easyread/web/js/common/settings.js").read_text(encoding="utf-8")
        self.assertIn('"opencode-go"', settings)

    def test_opencode_zen_pay_as_you_go_is_paid_preset(self):
        by_id = {p["id"]: p for p in PRESETS}
        preset = by_id["opencode"]
        self.assertEqual(preset["group"], "paid")
        self.assertEqual(preset["base_url"], "https://opencode.ai/zen/v1")
        self.assertTrue(preset["key"])
        self.assertIn(preset["model"], preset["models"])


if __name__ == "__main__":
    unittest.main()

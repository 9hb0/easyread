import json
import io
import threading
import unittest
import urllib.error
from unittest import mock

from easyread import chat, chat_models, config, engines


class FakeResponse:
    def __init__(self, body):
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class FakeStreamResponse(FakeResponse):
    def __iter__(self):
        return iter(self.body.splitlines(keepends=True))


class ModelConfigTest(unittest.TestCase):
    def test_custom_chat_model_uses_its_endpoint_key_and_reasoning(self):
        cfg = {
            "openai": {"base_url": "https://global.example/v1", "api_key": "global"},
            "chat": {"default": "relay", "models": [{
                "id": "relay", "name": "中转模型", "engine": "openai",
                "base_url": "https://relay.example/v1", "api_key": "relay-key",
                "model": "custom-model", "reasoning_effort": "high",
            }]},
        }
        ecfg, model = chat_models.engine_cfg(cfg, "relay")
        self.assertEqual(ecfg["openai"]["base_url"], "https://relay.example/v1")
        self.assertEqual(ecfg["openai"]["api_key"], "relay-key")
        self.assertEqual(ecfg["openai"]["model"], "custom-model")
        self.assertEqual(ecfg["openai"]["reasoning_effort"], "high")
        self.assertEqual(model["id"], "relay")

    def test_masked_custom_key_preserves_saved_value(self):
        old = [{"id": "relay", "engine": "openai", "model": "old", "api_key": "secret"}]
        out = chat_models.sanitize([{"id": "relay", "engine": "openai", "base_url": "https://relay.example/v1", "model": "new", "api_key": "••••"}], old)
        self.assertEqual(out[0]["api_key"], "secret")
        self.assertEqual(out[0]["model"], "new")

    def test_list_models_reads_openai_compatible_endpoint(self):
        response = FakeResponse(json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}))
        with mock.patch.object(engines.urllib.request, "urlopen", return_value=response) as open_url:
            models = engines.list_models("https://relay.example/v1/", "relay-key")
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://relay.example/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer relay-key")
        self.assertEqual(models, ["model-a", "model-b"])

    def test_root_openai_base_url_defaults_to_v1(self):
        self.assertEqual(engines.openai_base("https://www.aivalux.com"), "https://www.aivalux.com/v1")
        self.assertEqual(engines.openai_base("https://ark.example/api/plan/v3"), "https://ark.example/api/plan/v3")

    def test_empty_openai_stream_is_reported_as_error(self):
        response = FakeStreamResponse("data: [DONE]\n\n")
        cfg = {"base_url": "https://relay.example", "model": "custom-model", "api_key": "relay-key"}
        with mock.patch.object(engines.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(engines.EngineError, "没有返回回答内容"):
                list(chat._stream_openai(cfg, "hello", threading.Event()))

    def test_chat_retries_without_effort_when_relay_rejects_summary_field(self):
        error = urllib.error.HTTPError(
            "https://relay.example/v1/chat/completions", 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"message":"json: unknown field \"summary\""}}'),
        )
        response = FakeStreamResponse('data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
        cfg = {"base_url": "https://relay.example/v1", "model": "custom-model", "api_key": "relay-key", "reasoning_effort": "high"}
        with mock.patch.object(engines.urllib.request, "urlopen", side_effect=[error, response]) as open_url:
            self.assertEqual("".join(chat._stream_openai(cfg, "hello", threading.Event())), "OK")
        first = json.loads(open_url.call_args_list[0].args[0].data)
        second = json.loads(open_url.call_args_list[1].args[0].data)
        self.assertEqual(first["reasoning_effort"], "high")
        self.assertNotIn("reasoning_effort", second)

    def test_openai_request_includes_reasoning_effort(self):
        response = FakeResponse(json.dumps({"choices": [{"message": {"content": "ok"}}]}))
        cfg = {"base_url": "https://relay.example/v1", "model": "custom-model",
               "api_key": "relay-key", "reasoning_effort": "high"}
        with mock.patch.object(engines.urllib.request, "urlopen", return_value=response) as open_url:
            self.assertEqual(engines.run_openai(cfg, "hello", [], None), "ok")
        body = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(body["model"], "custom-model")
        self.assertEqual(body["reasoning_effort"], "high")

    def test_public_config_does_not_expose_chat_key(self):
        public = config.public({"openai": {}, "chat": {"models": [{"id": "relay", "api_key": "secret"}]}})
        self.assertNotIn("api_key", public["chat"]["models"][0])
        self.assertTrue(public["chat"]["models"][0]["has_key"])

    def test_custom_chat_key_marks_model_ready(self):
        listing = chat_models.listing({
            "claude": {"command": "claude"},
            "codex": {"command": "codex"},
            "openai": {},
            "chat": {"models": [{"id": "relay", "engine": "openai", "base_url": "https://relay.example/v1", "api_key": "secret", "model": "m"}]},
        })
        self.assertTrue(listing["models"][0]["ready"])


if __name__ == "__main__":
    unittest.main()

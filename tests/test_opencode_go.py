"""OpenCode Go 网关要求、任务会话和协议兼容性回归。"""
import io
import json
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from easyread import chat, engines
from easyread.presets import OPENCODE_GO_CHAT_MODELS, OPENCODE_GO_MESSAGES_MODELS, OPENCODE_GO_RESPONSES_MODELS


BASE = "https://opencode.ai/inference/go/v1"
OLD_BASE = "https://opencode.ai/zen/go/v1"


def reply(stream=False):
    if stream:
        return io.BytesIO(b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
    return io.BytesIO(b'{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}]}')


class OpenCodeGoTest(unittest.TestCase):
    def setUp(self):
        self.options = {"base_url": OLD_BASE, "model": "glm-5.3-flash", "api_key": "test-key"}

    def assert_gateway_headers(self, request):
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertTrue(headers["user-agent"].startswith("easyread/"))
        self.assertTrue(headers["x-opencode-session"])
        self.assertEqual(headers["authorization"], "Bearer test-key")
        return headers["x-opencode-session"]

    def test_translation_satisfies_gateway_and_migrates_legacy_url(self):
        def gateway(request, **kwargs):
            self.assert_gateway_headers(request)
            self.assertEqual(request.full_url, BASE + "/chat/completions")
            return reply()
        with mock.patch.object(engines.urllib.request, "urlopen", side_effect=gateway):
            self.assertEqual(engines.run_openai(self.options, "hello", []), "OK")

    def test_stream_retry_keeps_session_and_accept_header(self):
        error = urllib.error.HTTPError(BASE, 400, "Bad Request", {},
                                       io.BytesIO(b'{"error":{"message":"unknown field summary"}}'))
        options = dict(self.options, reasoning_effort="high", session_id="conversation-1")
        with mock.patch.object(engines.urllib.request, "urlopen", side_effect=[error, reply(True)]) as send:
            self.assertEqual("".join(chat._stream_openai(options, "hello", threading.Event())), "OK")
        for call in send.call_args_list:
            request = call.args[0]
            self.assertEqual(self.assert_gateway_headers(request), "conversation-1")
            self.assertEqual(request.get_header("Accept"), "text/event-stream")

    def test_translation_sessions_stable_per_task_and_isolated_per_paper(self):
        cfg = {"engine": "openai", "openai": self.options, "_task_id": "job-a"}
        sessions = []
        def gateway(request, **kwargs):
            sessions.append(self.assert_gateway_headers(request))
            return reply()
        with mock.patch.object(engines.urllib.request, "urlopen", side_effect=gateway):
            for root, task in [("paper-a", "job-a"), ("paper-a", "job-a"),
                               ("paper-b", "job-a"), ("paper-a", "job-b")]:
                engines.run(dict(cfg, _task_id=task), "hello", Path(root))
        self.assertEqual(sessions[0], sessions[1])
        self.assertEqual(len(set(sessions)), 3)
        self.assertNotIn("session_id", self.options)

    def test_model_refresh_returns_every_go_model_and_drops_pay_as_you_go_only(self):
        data = {"data": [{"id": model} for model in ["gpt-6-luna", "cat-jam-free", "glm-5.3-flash", "kimi-k3", "minimax-m3", "grok-4.7"]]}
        with mock.patch.object(engines.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(data).encode())) as send:
            models = engines.list_models(OLD_BASE + "/models", "test-key")
        request = send.call_args.args[0]
        self.assert_gateway_headers(request)
        self.assertEqual(request.full_url, BASE + "/models")
        self.assertEqual(set(models), {"gpt-6-luna", "glm-5.3-flash", "kimi-k3", "minimax-m3", "grok-4.7"})
        self.assertNotIn("cat-jam-free", models)

    def test_other_providers_keep_client_id_but_not_go_session_or_model_restrictions(self):
        for base in ["https://relay.example/v1", "https://opencode.ai/zen/v1", "https://opencode.ai.example/inference/go/v1"]:
            with self.subTest(base=base):
                headers = engines.api_headers(base, "key")
                self.assertEqual(headers["Authorization"], "Bearer key")
                self.assertTrue(headers["User-Agent"].startswith("easyread/"))
                self.assertNotIn("x-opencode-session", headers)
                self.assertEqual(engines.api_protocol(base, "gpt-6-luna"), "chat")
                self.assertEqual(engines.openai_base(base), base)

    def test_every_go_model_routes_to_its_documented_protocol(self):
        for model in OPENCODE_GO_RESPONSES_MODELS:
            self.assertEqual(engines.api_protocol(BASE, model), "responses")
        for model in OPENCODE_GO_MESSAGES_MODELS:
            self.assertEqual(engines.api_protocol(BASE, model), "messages")
        for model in OPENCODE_GO_CHAT_MODELS:
            self.assertEqual(engines.api_protocol(BASE, model), "chat")


class GoProtocolTest(unittest.TestCase):
    def capture(self, response_body, options, images=()):
        with mock.patch.object(engines.urllib.request, "urlopen", return_value=io.BytesIO(response_body)) as send:
            result = engines.run_openai(options, "hello", list(images))
        return result, send.call_args.args[0]

    def test_gpt_uses_responses_body_and_reads_output_text(self):
        options = dict(self_options(), base_url=BASE, model="gpt-6-luna", reasoning_effort="xhigh")
        body = json.dumps({"model": "gpt-6-luna", "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                           "output": [{"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]})
        text, req = self.capture(body.encode(), options)
        sent = json.loads(req.data)
        self.assertEqual(text, "OK")
        self.assertEqual(req.full_url, BASE + "/responses")
        self.assertEqual(sent["input"][0]["content"], [{"type": "input_text", "text": "hello"}])
        self.assertEqual(sent["reasoning"], {"effort": "high"})  # xhigh/max 收到 GPT 只认的档位
        self.assertNotIn("temperature", sent)  # GPT 拒绝 temperature

    def test_claude_uses_messages_body_and_skips_thinking_blocks(self):
        options = dict(self_options(), base_url=BASE, model="claude-haiku-5-5")
        body = json.dumps({"model": "claude-haiku-5-5", "stop_reason": "end_turn", "usage": {"input_tokens": 4, "output_tokens": 1},
                           "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "OK"}]})
        text, req = self.capture(body.encode(), options)
        sent = json.loads(req.data)
        self.assertEqual(text, "OK")
        self.assertEqual(req.full_url, BASE + "/messages")
        self.assertEqual(sent["max_tokens"], 32000)
        self.assertEqual(sent["messages"][0]["content"], [{"type": "text", "text": "hello"}])

    def test_truncated_responses_reports_output_limit(self):
        options = dict(self_options(), base_url=BASE, model="gpt-6-luna")
        body = json.dumps({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []})
        with self.assertRaisesRegex(engines.EngineError, "截断"):
            self.capture(body.encode(), options)

    def test_vision_only_sent_to_models_that_accept_images(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "p.jpg"
            img.write_bytes(b"\xff\xd8fake")
            gpt, req = self.capture(json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "white"}]}]}).encode(),
                                    dict(self_options(), base_url=BASE, model="gpt-6-luna", vision=True), [img])
            parts = json.loads(req.data)["input"][0]["content"]
            self.assertEqual(gpt, "white")
            self.assertEqual(parts[1]["type"], "input_image")
            claude, req = self.capture(json.dumps({"content": [{"type": "text", "text": "white"}]}).encode(),
                                        dict(self_options(), base_url=BASE, model="claude-haiku-5-5", vision=True), [img])
            blocks = json.loads(req.data)["messages"][0]["content"]
            self.assertEqual(claude, "white")
            self.assertEqual(blocks[0]["type"], "image")
            # GLM 只发文字，不给它塞图片
            _, req = self.capture(json.dumps({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}).encode(),
                                   dict(self_options(), base_url=BASE, model="glm-5.3-flash", vision=True), [img])
            self.assertEqual(json.loads(req.data)["messages"][0]["content"], "hello")

    def test_stream_reads_both_protocols_and_ignores_thinking_events(self):
        responses_sse = "".join([
            'event: response.created\ndata: {"type":"response.created"}\n\n',
            'data: {"type":"response.reasoning_summary_text.delta","delta":"thinking"}\n\n',
            'data: {"type":"response.output_text.delta","delta":"你"}\n\n',
            'data: {"type":"response.output_text.delta","delta":"好"}\n\n']).encode()
        messages_sse = "".join([
            'event: content_block_start\ndata: {"type":"content_block_start","content_block":{"type":"thinking"}}\n\n',
            'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"hmm"}}\n\n',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"你"}}\n\n',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"好"}}\n\n']).encode()
        for model, sse, path in [("gpt-6-luna", responses_sse, "/responses"), ("claude-haiku-5-5", messages_sse, "/messages")]:
            with self.subTest(model=model):
                options = dict(self_options(), base_url=BASE, model=model)
                with mock.patch.object(engines.urllib.request, "urlopen", return_value=io.BytesIO(sse)) as send:
                    out = "".join(chat._stream_openai(options, "hello", threading.Event()))
                self.assertEqual(out, "你好")
                self.assertEqual(send.call_args.args[0].full_url, BASE + path)

    def test_stream_error_event_becomes_engine_error(self):
        sse = b'data: {"type":"response.failed","response":{"error":{"message":"quota exceeded"}}}\n\n'
        with mock.patch.object(engines.urllib.request, "urlopen", return_value=io.BytesIO(sse)):
            with self.assertRaisesRegex(engines.EngineError, "quota exceeded"):
                list(chat._stream_openai(dict(self_options(), base_url=BASE, model="gpt-6-luna"), "hello", threading.Event()))

    def test_empty_go_stream_reports_model_hint(self):
        with mock.patch.object(engines.urllib.request, "urlopen", return_value=io.BytesIO(b'data: {"type":"ping"}\n\n')):
            with self.assertRaisesRegex(engines.EngineError, "没有返回回答内容"):
                list(chat._stream_openai(dict(self_options(), base_url=BASE, model="claude-haiku-5-5"), "hello", threading.Event()))


def self_options():
    return {"base_url": BASE, "model": "glm-5.3-flash", "api_key": "test-key", "timeout": 5}


if __name__ == "__main__":
    unittest.main()

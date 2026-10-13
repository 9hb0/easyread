"""翻译 / 回答用的模型后端。

- claude：本机的 Claude Code 无头模式（claude -p），用你已有的登录，不需要 Key；能自己读原页图核对公式和表格。
- codex：本机的 Codex CLI（codex exec），同样用已有登录，原页图作为附件发过去。
- openai：任何 OpenAI 兼容接口（Ollama、智谱、硅基流动、DeepSeek、Gemini……），在设置里填地址、模型和 Key。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path

from . import __version__

_tls = threading.local()


class EngineError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


ENGINE_NAMES = {"claude": "Claude Code", "codex": "Codex CLI", "openai": "API", "none": "不翻译"}
REASONING_EFFORTS = ("", "low", "medium", "high", "xhigh", "max")


def run(cfg: dict, prompt: str, cwd: Path, images: list[Path] | None = None, cancel: threading.Event | None = None) -> str:
    _tls.usage = None
    _tls.model = None
    engine = cfg.get("engine")
    if engine == "claude":
        return run_claude(cfg["claude"], prompt, cwd, cancel)
    if engine == "codex":
        return run_codex(cfg["codex"], prompt, cwd, images or [], cancel)
    if engine == "openai":
        c = dict(cfg["openai"])
        c["session_id"] = c.get("session_id") or api_session(cwd, cfg.get("_task_id") or "translation")
        return run_openai(c, prompt, images or [], cancel)
    raise EngineError("没有配置翻译引擎（设置 → 翻译引擎）")


def last_usage() -> dict | None:
    """最近一次调用报告的用量；引擎未提供时为 None，不猜测。"""
    return getattr(_tls, "usage", None)


def last_model() -> str | None:
    return getattr(_tls, "model", None)


def image_mode(cfg: dict) -> str:
    """提示词里怎么说原页图：claude 自己用 Read 读；codex 和能看图的接口作为附件；其余没有图。"""
    engine = cfg.get("engine")
    if engine == "claude":
        return "claude"
    if engine == "codex" or (engine == "openai" and cfg["openai"].get("vision")):
        return "attached"
    return "text"


def who(cfg: dict) -> str:
    engine = cfg.get("engine")
    if engine == "openai":
        return cfg["openai"].get("model") or "API"
    return {"claude": "claude", "codex": "codex"}.get(engine, "")


# ---------- 本机 CLI ----------
_NO_WINDOW = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
_CLAUDE_ARGS = ["--output-format", "json", "--allowedTools", "Read", "--strict-mcp-config",
                "--disable-slash-commands", "--no-session-persistence"]


def claude_path(c: dict) -> str | None:
    return shutil.which(c.get("command") or "claude")


def codex_path(c: dict) -> str | None:
    return shutil.which(c.get("command") or "codex")


def _popen(args: list[str], cwd: Path):
    return subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)


def run_claude(c: dict, prompt: str, cwd: Path, cancel=None) -> str:
    exe = claude_path(c)
    if not exe:
        raise EngineError(f"找不到 Claude Code 命令：{c.get('command') or 'claude'}（先装好并登录 Claude Code）")
    args = [exe, "-p", *_CLAUDE_ARGS]
    if c.get("model"):
        args += ["--model", c["model"]]
    if c.get("reasoning_effort"):
        args += ["--effort", c["reasoning_effort"]]
    args += list(c.get("extra_args") or [])
    out = _communicate(_popen(args, cwd), prompt, int(c.get("timeout") or 1200), cancel)
    try:
        res = json.loads(out)
    except json.JSONDecodeError:
        raise EngineError(f"Claude Code 输出不是 JSON：{out[:300]}")
    if res.get("is_error") or res.get("subtype", "success") != "success":
        msg = str(res.get("result") or res.get("terminal_reason") or res.get("subtype"))
        if "limit" in msg.lower():
            msg += "（用量到上限了，等额度恢复后点“重试”，或在设置里换个引擎）"
        raise EngineError(f"Claude Code 出错：{msg}")
    usage = res.get("usage") or {}
    if "input_tokens" in usage and "output_tokens" in usage:
        prompt = sum(usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        output = usage["output_tokens"]
        _tls.usage = {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output}
    models = res.get("modelUsage") or {}
    _tls.model = next(iter(models), None)
    return res.get("result") or ""


def run_codex(c: dict, prompt: str, cwd: Path, images: list[Path], cancel=None) -> str:
    exe = codex_path(c)
    if not exe:
        raise EngineError(f"找不到 Codex 命令：{c.get('command') or 'codex'}（先装好并登录 Codex CLI）")
    fd, last = tempfile.mkstemp(suffix=".txt", prefix="easyread-codex-")
    os.close(fd)
    args = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "--color", "never", "--json", "-o", last]
    if c.get("model"):
        args += ["--model", c["model"]]
    if c.get("reasoning_effort"):
        args += ["-c", f"model_reasoning_effort={json.dumps(c['reasoning_effort'])}"]
    for img in images:
        args += ["-i", str(img)]
    args += list(c.get("extra_args") or []) + ["-"]
    try:
        out = _communicate(_popen(args, cwd), prompt, int(c.get("timeout") or 1200), cancel)
        text = Path(last).read_text(encoding="utf-8", errors="replace").strip()
    finally:
        Path(last).unlink(missing_ok=True)
    if not text:
        raise EngineError("Codex 没有给出结果：" + (out or "")[-300:])
    usages, complete = [], True
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens")):
            usages.append(usage)
        else:  # 任一轮缺失用量就整次作废：部分合计会低估成本
            complete = False
    if usages and complete:
        prompt = sum(u["input_tokens"] for u in usages)  # cached_input_tokens is already included
        output = sum(u["output_tokens"] for u in usages)
        _tls.usage = {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output}
    _tls.model = c.get("model") or None
    return text


def _communicate(proc, stdin_text: str, timeout: int, cancel) -> str:
    result = {}

    def talk():
        result["out"], result["err"] = proc.communicate(stdin_text)

    t = threading.Thread(target=talk, daemon=True)
    t.start()
    waited = 0.0
    while t.is_alive():
        t.join(0.5)
        waited += 0.5
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise Cancelled()
        if waited > timeout:
            proc.kill()
            raise EngineError(f"超过 {timeout} 秒没有结果")
    if proc.returncode not in (0, None) and not result.get("out"):
        raise EngineError((result.get("err") or "")[-500:] or f"退出码 {proc.returncode}")
    return result.get("out", "")


# ---------- OpenAI 兼容接口 ----------
def openai_base(base_url: str) -> str:
    """补齐常见的 OpenAI 兼容根地址；带具体路径的中转站保持原样。"""
    base = (base_url or "").rstrip("/")
    parts = urlsplit(base)
    if parts.hostname == "opencode.ai" and parts.path == "/zen/go/v1":
        return urlunsplit((parts.scheme, parts.netloc, "/inference/go/v1", parts.query, parts.fragment))
    if parts.scheme and parts.netloc and parts.path in ("", "/"):
        return urlunsplit((parts.scheme, parts.netloc, "/v1", parts.query, parts.fragment))
    return base


def api_session(cwd: Path, name: str) -> str:
    """同一论文的同一对话 / 翻译任务复用 ID，不向服务商暴露本机路径。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"easyread:{cwd.resolve()}:{name}"))


def is_opencode_go(base_url: str) -> bool:
    parts = urlsplit(openai_base(base_url))
    return parts.hostname == "opencode.ai" and parts.path.rstrip("/") == "/inference/go/v1"


def api_headers(base_url: str, api_key: str = "", session_id: str = "") -> dict:
    # 所有接口都自报客户端：Cloudflare 会对 Python-urllib 这类默认 UA 直接返回 1010 拦截。
    headers = {"User-Agent": "easyread/" + __version__}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    if is_opencode_go(base_url):
        headers["x-opencode-session"] = session_id or str(uuid.uuid4())
    return headers


def api_protocol(base_url: str, model: str) -> str:
    """Go 把模型分在 chat/completions、responses、messages 三套协议上，按模型名判断。"""
    if not is_opencode_go(base_url):
        return "chat"
    from .presets import OPENCODE_GO_MESSAGES_MODELS, OPENCODE_GO_RESPONSES_MODELS
    if model in OPENCODE_GO_MESSAGES_MODELS:
        return "messages"
    if model in OPENCODE_GO_RESPONSES_MODELS:
        return "responses"
    return "chat"


def supports_vision(base_url: str, model: str) -> bool:
    if not is_opencode_go(base_url):
        return True
    from .presets import OPENCODE_GO_VISION_MODELS
    return model in OPENCODE_GO_VISION_MODELS


def effort_for_responses(effort: str) -> str:
    return {"xhigh": "high", "max": "high"}.get(effort or "", effort or "")


def vision_images(images: list[Path]) -> list[str]:
    return ["data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode() for p in images]


def http_hint(code: int) -> str:
    return {401: "（Key 不对或过期了）", 402: "（余额不足）", 403: "（没有权限用这个模型）",
            404: "（地址或模型名不对）", 429: "（被限流了，稍后重试或换个模型）"}.get(code, "")


def post_json(url: str, body: dict, headers: dict, timeout: int, cancel=None) -> dict:
    """POST 一个 JSON 并解析响应；限流、服务端错误和网络抖动会重试。"""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    for attempt in range(4):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                _sleep(float(e.headers.get("Retry-After") or 0) or 5 * 2 ** attempt, cancel)
                continue
            raise EngineError(f"接口返回 {e.code}{http_hint(e.code)}：{detail}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt < 2:
                _sleep(5, cancel)
                continue
            raise EngineError(f"连不上接口：{e}")
    raise EngineError("接口没有返回结果")


def _finish(text: str, finish_reason: str) -> str:
    if finish_reason in ("length", "max_tokens"):
        raise EngineError("模型输出被截断了（超过它的输出长度上限）。在设置里把“每次交给模型的页数”调成 1 页再试。")
    if not isinstance(text, str) or not text.strip():
        raise EngineError(f"模型返回空内容（finish_reason={finish_reason or 'unknown'}，用量={_tls.usage}）")
    return text


def run_openai(c: dict, prompt: str, images: list[Path], cancel=None) -> str:
    base = openai_base(c.get("base_url") or "")
    if not base or not c.get("model"):
        raise EngineError("API 没填地址或模型（设置 → 翻译引擎）")
    if api_protocol(base, c["model"]) == "responses":
        return run_responses(c, base, prompt, images, cancel)
    if api_protocol(base, c["model"]) == "messages":
        return run_messages(c, base, prompt, images, cancel)
    content = prompt
    if c.get("vision") and images and supports_vision(base, c["model"]):
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()}}
            for p in images]
    body = {"model": c["model"], "temperature": 0.2, "messages": [{"role": "user", "content": content}]}
    if c.get("reasoning_effort"):
        body["reasoning_effort"] = c["reasoning_effort"]
    headers = {"Content-Type": "application/json", **api_headers(base, c.get("api_key") or "", c.get("session_id") or "")}
    res = post_json(base + "/chat/completions", body, headers, int(c.get("timeout") or 600), cancel)
    try:
        choice = res["choices"][0]
        _tls.usage = res.get("usage") or None
        _tls.model = res.get("model")
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise EngineError(f"接口返回格式不对：{str(res)[:300]}")
    return _finish(text, choice.get("finish_reason") or "")


def run_responses(c: dict, base: str, prompt: str, images: list[Path], cancel=None) -> str:
    """OpenAI Responses 协议：GPT / Grok 走这个接口，不接受 temperature。"""
    content = [{"type": "input_text", "text": prompt}]
    if c.get("vision") and images and supports_vision(base, c["model"]):
        content += [{"type": "input_image", "image_url": url} for url in vision_images(images)]
    body = {"model": c["model"], "input": [{"role": "user", "content": content}]}
    if c.get("reasoning_effort"):
        body["reasoning"] = {"effort": effort_for_responses(c["reasoning_effort"])}
    headers = {"Content-Type": "application/json", **api_headers(base, c.get("api_key") or "", c.get("session_id") or "")}
    res = post_json(base + "/responses", body, headers, int(c.get("timeout") or 600), cancel)
    if res.get("error"):
        raise EngineError(f"接口返回错误：{str(res['error'])[:300]}")
    _tls.model = res.get("model")
    usage = res.get("usage") or None
    _tls.usage = {"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"),
                  "total_tokens": usage.get("total_tokens")} if isinstance(usage, dict) else None
    text = "".join(part.get("text") or "" for item in (res.get("output") or []) if item.get("type") == "message"
                   for part in (item.get("content") or []) if part.get("type") == "output_text")
    reason = (res.get("incomplete_details") or {}).get("reason") or ""
    return _finish(text, "length" if reason == "max_output_tokens" else (res.get("status") or ""))


def run_messages(c: dict, base: str, prompt: str, images: list[Path], cancel=None) -> str:
    """Anthropic Messages 协议：Claude / Qwen / MiniMax 走这个接口，max_tokens 必填。"""
    content = []
    if c.get("vision") and images and supports_vision(base, c["model"]):
        content += [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                   "data": url.split(",", 1)[1]}} for url in vision_images(images)]
    content.append({"type": "text", "text": prompt})
    body = {"model": c["model"], "max_tokens": int(c.get("max_tokens") or 32000),
            "messages": [{"role": "user", "content": content}]}
    headers = {"Content-Type": "application/json", **api_headers(base, c.get("api_key") or "", c.get("session_id") or "")}
    res = post_json(base + "/messages", body, headers, int(c.get("timeout") or 600), cancel)
    if res.get("type") == "error":
        raise EngineError(f"接口返回错误：{str(res.get('error'))[:300]}")
    _tls.model = res.get("model")
    usage = res.get("usage") or {}
    _tls.usage = {"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens")}
    # 推理模型会先回 thinking 块，只取正文 text 块
    text = "".join(block.get("text") or "" for block in (res.get("content") or []) if block.get("type") == "text")
    return _finish(text, res.get("stop_reason") or "")


def _api_root(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    return openai_base(base).rstrip("/")


def list_models(base_url: str, api_key: str = "") -> list[str]:
    """读取 OpenAI 兼容中转站的模型列表，不记录 Key。"""
    root = _api_root(base_url)
    if not root:
        raise EngineError("没填 BASE_URL")
    key = api_key if api_key and not api_key.startswith("••••") else ""
    headers = {"Accept": "application/json", **api_headers(root, key)}
    req = urllib.request.Request(root + "/models", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise EngineError(f"读取模型列表失败 {e.code}：{detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise EngineError(f"读取模型列表失败：{e}")
    rows = data.get("data") or data.get("models") or []
    out = []
    for row in rows:
        value = row.get("id") if isinstance(row, dict) else row
        if value and str(value) not in out:
            out.append(str(value))
    if is_opencode_go(root):
        from .presets import OPENCODE_GO_MODELS
        out = [model for model in OPENCODE_GO_MODELS if model in out]  # /models 混进了按量付费的模型，只留 Go 的
    return out


def _sleep(seconds: float, cancel) -> None:
    end = time.time() + min(seconds, 90)
    while time.time() < end:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        time.sleep(0.5)


def parse_json(text: str):
    """从模型输出里取出 JSON（容忍 ```json 围栏和前后废话）。"""
    t = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()  # 推理模型（deepseek-r1、qwen3）先输出的思考过程
    # 先取最外层的 { … }：译文里可能本身带代码块（论文附录的 PyTorch 代码），按 ``` 围栏切会切到半截
    bodies = []
    for s in (t, *(m.group(1).strip() for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", t))):
        start = min([i for i in (s.find("{"), s.find("[")) if i >= 0], default=-1)
        if start >= 0:
            bodies.append(s[start:max(s.rfind("}"), s.rfind("]")) + 1])
    if not bodies:
        raise EngineError("模型输出里没有 JSON：" + text[:200])
    first = None
    for body in bodies:
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            first = first or e
    body = bodies[0]
    # 常见毛病：TeX 反斜杠没写成两个（\alpha、\sum）、字符串里有原样换行
    fixed = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", body)
    try:
        return json.loads(fixed, strict=False)
    except json.JSONDecodeError:
        raise EngineError(f"模型输出的 JSON 格式有错（{first}），会自动重试")


def _version(exe: str) -> str:
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                              creationflags=_NO_WINDOW).stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return ""


def test(cfg: dict) -> dict:
    """设置页“测试”按钮：真的让模型回一句，确认引擎能用。"""
    engine = cfg.get("engine")
    if engine in ("claude", "codex"):
        exe = (claude_path if engine == "claude" else codex_path)(cfg[engine])
        if not exe:
            return {"ok": False, "message": f"找不到 {engine} 命令，先安装并登录"}
    if engine == "none":
        return {"ok": True, "message": "未启用自动翻译"}
    try:
        out = run(cfg, '只回复 JSON，不要别的文字：{"ok": true}', Path(tempfile.gettempdir()), None, None)
        parse_json(out)
        return {"ok": True, "message": "可以用：" + out.strip()[:40]}
    except (EngineError, Cancelled) as e:
        return {"ok": False, "message": str(e)[:300]}

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
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path


class EngineError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


ENGINE_NAMES = {"claude": "Claude Code", "codex": "Codex CLI", "openai": "API", "none": "不翻译"}
REASONING_EFFORTS = ("", "low", "medium", "high", "xhigh", "max")


def run(cfg: dict, prompt: str, cwd: Path, images: list[Path] | None = None, cancel: threading.Event | None = None) -> str:
    engine = cfg.get("engine")
    if engine == "claude":
        return run_claude(cfg["claude"], prompt, cwd, cancel)
    if engine == "codex":
        return run_codex(cfg["codex"], prompt, cwd, images or [], cancel)
    if engine == "openai":
        return run_openai(cfg["openai"], prompt, images or [], cancel)
    raise EngineError("没有配置翻译引擎（设置 → 翻译引擎）")


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
    return res.get("result") or ""


def run_codex(c: dict, prompt: str, cwd: Path, images: list[Path], cancel=None) -> str:
    exe = codex_path(c)
    if not exe:
        raise EngineError(f"找不到 Codex 命令：{c.get('command') or 'codex'}（先装好并登录 Codex CLI）")
    fd, last = tempfile.mkstemp(suffix=".txt", prefix="easyread-codex-")
    os.close(fd)
    args = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "--color", "never", "-o", last]
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
    if parts.scheme and parts.netloc and parts.path in ("", "/"):
        return urlunsplit((parts.scheme, parts.netloc, "/v1", parts.query, parts.fragment))
    return base


def run_openai(c: dict, prompt: str, images: list[Path], cancel=None) -> str:
    base = openai_base(c.get("base_url") or "")
    if not base or not c.get("model"):
        raise EngineError("API 没填地址或模型（设置 → 翻译引擎）")
    content = prompt
    if c.get("vision") and images:
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()}}
            for p in images]
    body = {"model": c["model"], "temperature": 0.2, "messages": [{"role": "user", "content": content}]}
    if c.get("reasoning_effort"):
        body["reasoning_effort"] = c["reasoning_effort"]
    headers = {"Content-Type": "application/json"}
    if c.get("api_key"):
        headers["Authorization"] = "Bearer " + c["api_key"]
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), headers=headers)
    res = None
    for attempt in range(4):  # 限流、服务端错误、网络抖动：等一会儿再试
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            with urllib.request.urlopen(req, timeout=int(c.get("timeout") or 600)) as r:
                res = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                _sleep(float(e.headers.get("Retry-After") or 0) or 5 * 2 ** attempt, cancel)
                continue
            hint = {401: "（Key 不对或过期了）", 402: "（余额不足）", 403: "（没有权限用这个模型）",
                    404: "（地址或模型名不对）", 429: "（被限流了，稍后重试或换个模型）"}.get(e.code, "")
            raise EngineError(f"接口返回 {e.code}{hint}：{detail}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt < 2:
                _sleep(5, cancel)
                continue
            raise EngineError(f"连不上接口：{e}")
    try:
        choice = res["choices"][0]
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise EngineError(f"接口返回格式不对：{str(res)[:300]}")
    if choice.get("finish_reason") == "length":
        raise EngineError("模型输出被截断了（超过它的输出长度上限）。在设置里把“每次交给模型的页数”调成 1 页再试。")
    return text


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
    headers = {"Accept": "application/json"}
    if api_key and not api_key.startswith("••••"):
        headers["Authorization"] = "Bearer " + api_key
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

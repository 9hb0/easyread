"""“问 AI”用哪些模型：设置里一张短名单（默认 Claude Opus 5.5、Claude Sonnet 5.5、GPT），可以增删改。

每一项：{"id", "name", "engine": "claude" | "codex" | "openai", "model", "preset"（API 服务商）, "base_url"（自定义地址时）}
API 的 Key 用翻译引擎那边按服务商存的同一份（openai.keys），不用填两次。
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from . import engines
from .presets import PRESETS

# opus / sonnet 是 Claude Code 的别名：它会用自己支持的最新版（升级 Claude Code 后自动变成 Opus 5.5 等），
# 实际用的是哪个版本，第一次回答时记下来显示在名单上。
DEFAULT_MODELS = [
    {"id": "opus", "name": "Claude Opus", "engine": "claude", "model": "opus"},
    {"id": "sonnet", "name": "Claude Sonnet", "engine": "claude", "model": "sonnet"},
    {"id": "gpt", "name": "GPT", "engine": "codex", "model": ""},
]
_SEEN_PATH = None  # config.HOME / ".models-seen.json"，懒加载避免循环导入


def _seen_path():
    from . import config
    return config.HOME / ".models-seen.json"


def pretty(model_id: str) -> str:
    """claude-opus-5-5 → Claude Opus 5.5；claude-sonnet-5 → Claude Sonnet 5。"""
    m = re.fullmatch(r"claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?", model_id or "")
    if not m:
        return model_id
    return f"Claude {m.group(1).capitalize()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")


def remember(alias: str, actual: str) -> None:
    """记下别名实际对应的模型（Claude Code 在每次回答开头会报）。"""
    if not alias or not actual or alias == actual:
        return
    from .store import read_json, write_json_atomic
    seen = read_json(_seen_path(), {}) or {}
    if seen.get(alias) != actual:
        seen[alias] = actual
        write_json_atomic(_seen_path(), seen)


def actual_of(alias: str) -> str:
    from .store import read_json
    return (read_json(_seen_path(), {}) or {}).get(alias, "")
DEFAULT_CHAT = {"models": DEFAULT_MODELS, "default": "opus"}


def codex_default_model() -> str:
    """Codex CLI 没指定模型时用它自己配置里的（~/.codex/config.toml 的 model）。"""
    try:
        text = (Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"', text.split("[", 1)[0])
    return m.group(1) if m else ""


def models(cfg: dict) -> list[dict]:
    return list((cfg.get("chat") or {}).get("models") or DEFAULT_MODELS)


def find(cfg: dict, mid: str | None) -> dict:
    ms = models(cfg)
    want = mid or (cfg.get("chat") or {}).get("default")
    return next((m for m in ms if m.get("id") == want), ms[0] if ms else DEFAULT_MODELS[0])


def _key(cfg: dict, preset: str) -> str:
    o = cfg["openai"]
    keys = dict(o.get("keys") or {})
    if o.get("api_key"):
        keys.setdefault(o.get("preset") or "", o["api_key"])
    return keys.get(preset or "", "")


def engine_cfg(cfg: dict, mid: str | None) -> tuple[dict, dict]:
    """名单里的一项 → (engines 认的配置, 这一项)。"""
    m = find(cfg, mid)
    out = copy.deepcopy(cfg)
    out["engine"] = m["engine"]
    if m["engine"] in ("claude", "codex"):
        out[m["engine"]]["model"] = m.get("model") or ""
        out[m["engine"]]["reasoning_effort"] = m.get("reasoning_effort") or ""
    elif m["engine"] == "openai":
        p = next((x for x in PRESETS if x["id"] == m.get("preset")), None)
        out["openai"] = {**out["openai"], "preset": m.get("preset") or "", "vision": bool(m.get("vision")),
                         "base_url": m.get("base_url") or (p["base_url"] if p else out["openai"].get("base_url", "")),
                         "model": m.get("model") or (p["model"] if p else ""),
                         "api_key": m.get("api_key") or _key(cfg, m.get("preset") or ""),
                         "reasoning_effort": m.get("reasoning_effort") or ""}
    else:
        raise engines.EngineError(f"不认识的模型来源：{m.get('engine')}")
    return out, m


def label(m: dict) -> str:
    """面板上显示的名字：Claude 别名显示实际版本（Claude Opus 5），Codex 没填模型时带上它实际用的模型。"""
    if m.get("engine") == "claude" and m.get("model") in ("opus", "sonnet", "haiku") and actual_of(m["model"]):
        return pretty(actual_of(m["model"]))
    if m.get("engine") == "codex" and (not m.get("name") or m.get("name") in ("GPT", m.get("model"))):
        from . import cli_models  # 用 Codex 里 /model 显示的名字，比如 GPT-6-Astra
        slug = m.get("model") or codex_default_model()
        return next((x["name"] for x in cli_models.codex()["models"] if x["id"] == slug), slug or "GPT")
    return m.get("name") or m.get("model") or "模型"


def listing(cfg: dict) -> dict:
    """给页面：名单 + 每项能不能用、来源说明。"""
    from .detect import detect, needs_key
    found = detect(cfg)
    out = []
    for m in models(cfg):
        e = m.get("engine")
        if e in ("claude", "codex"):
            ready = bool(found.get(e, {}).get("found"))
            source = "Claude Code" if e == "claude" else "Codex CLI"
            hint = "" if ready else f"本机没找到 {source}"
        else:
            p = next((x for x in PRESETS if x["id"] == m.get("preset")), None)
            ready = bool(m.get("api_key") or _key(cfg, m.get("preset") or "")) or not needs_key({"preset": m.get("preset"), "base_url": m.get("base_url", "")})
            source = p["name"] if p else "自定义接口"
            hint = "" if ready else f"还没填 {source} 的 Key（设置 → 问 AI → 改）"
        out.append({**{k: v for k, v in m.items() if k != "api_key"}, "label": label(m), "source": source, "ready": ready, "hint": hint,
                    "has_key": bool(m.get("api_key")) or bool(_key(cfg, m.get("preset") or "")),
                    "detail": (actual_of(m.get("model", "")) or m.get("model")) if e == "claude"
                    else m.get("model") or ((codex_default_model() + "（跟随 Codex 默认）") if e == "codex" and codex_default_model() else "")})
    default = (cfg.get("chat") or {}).get("default") or (out[0]["id"] if out else "")
    return {"models": out, "default": default, "presets": [{"id": p["id"], "name": p["name"], "models": p.get("models", []), "vision": bool(p.get("vision"))} for p in PRESETS]}


def sanitize(items: list[dict], previous: list[dict] | None = None) -> list[dict]:
    """设置页提交的名单：去掉空项、补 id。"""
    out, seen = [], set()
    old = {str(m.get("id")): m for m in (previous or [])}
    for k, m in enumerate(items or []):
        e = m.get("engine")
        if e not in ("claude", "codex", "openai") or (e == "openai" and not (m.get("preset") or m.get("base_url"))):
            continue
        mid = re.sub(r"[^\w\-]", "-", str(m.get("id") or f"m{k}"))[:40] or f"m{k}"
        while mid in seen:
            mid += "-2"
        seen.add(mid)
        key = m.get("api_key")
        if not key or str(key).startswith("••••"):
            key = (old.get(mid) or {}).get("api_key", "")
        out.append({"id": mid, "name": str(m.get("name") or m.get("model") or "模型")[:40], "engine": e,
                    "model": str(m.get("model") or "")[:120], "preset": str(m.get("preset") or ""),
                    "base_url": str(m.get("base_url") or "")[:300], "api_key": str(key or "")[:500],
                    "reasoning_effort": str(m.get("reasoning_effort") or "")[:20], "vision": bool(m.get("vision"))})
    return out or copy.deepcopy(DEFAULT_MODELS)

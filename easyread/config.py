"""配置和数据位置。

从源码目录运行时，数据就放在源码目录（library/、config.json）；pip 安装后放在 ~/EasyRead。
环境变量 EASYREAD_HOME 改数据目录，EASYREAD_LIBRARY 临时指定文献库（测试、多库）。
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from .chat_models import DEFAULT_CHAT
from .deepread import DEFAULT_PROMPT
from .presets import PRESET_GROUPS, PRESETS  # noqa: F401
from .store import read_json, write_json_atomic

PACKAGE = Path(__file__).resolve().parent
WEB = PACKAGE / "web"
_SOURCE = PACKAGE.parent
HOME = Path(os.environ.get("EASYREAD_HOME") or (_SOURCE if (_SOURCE / "pyproject.toml").exists() else Path.home() / "EasyRead"))
PROJECT = HOME  # 旧名，cli 里还在用
CONFIG_PATH = HOME / "config.json"
LOG_PATH = HOME / "easyread.log"
SERVER_INFO = HOME / ".server.json"

DEFAULTS = {
    "library_dir": str(HOME / "library"),
    "port": 8765,
    "engine": "claude",          # claude | codex（本机 CLI 无头）| openai（任意 OpenAI 兼容接口）| none
    "auto_translate": True,      # 导入后自动开始翻译
    "batch_pages": 2,            # 每次交给模型的页数
    "concurrency": 1,            # 同时翻译几批
    "claude": {"command": "claude", "model": "", "reasoning_effort": "", "extra_args": [], "timeout": 1200},
    "codex": {"command": "codex", "model": "", "reasoning_effort": "", "extra_args": [], "timeout": 1200},
    "openai": {"preset": "", "base_url": "", "api_key": "", "model": "", "reasoning_effort": "", "vision": False, "timeout": 600},
    # 阅读页右侧“问 AI”的模型名单和默认模型，见 chat_models.py
    "chat": copy.deepcopy(DEFAULT_CHAT),
    "deepread": {"model": "", "prompt": DEFAULT_PROMPT},
}

def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def is_first_run() -> bool:
    return not CONFIG_PATH.exists()


def load() -> dict:
    cfg = _merge(DEFAULTS, read_json(CONFIG_PATH, {}) or {})
    lib = os.environ.get("EASYREAD_LIBRARY") or os.environ.get("COREAD_LIBRARY")
    if lib:  # 测试或多库时临时指定文献库
        cfg["library_dir"] = lib
    return cfg


def temp_library() -> bool:
    return bool(os.environ.get("EASYREAD_LIBRARY") or os.environ.get("COREAD_LIBRARY"))


def save(patch: dict) -> dict:
    cfg = _merge(_merge(DEFAULTS, read_json(CONFIG_PATH, {}) or {}), patch)
    write_json_atomic(CONFIG_PATH, cfg)
    return load()


def with_key(o: dict) -> dict:
    """页面提交的 openai 设置 → 要存的样子。每家服务商的 Key 分开存（keys[预设]），换来换去不用重填；
    页面发回来的打码 Key 或空 Key 表示不改。"""
    cur = load()["openai"]
    o = dict(o)
    key = o.pop("api_key", None)
    preset = o.get("preset", cur.get("preset")) or ""
    keys = dict(cur.get("keys") or {})
    if cur.get("api_key") and not keys:  # 旧配置只有一个 Key
        keys[cur.get("preset") or ""] = cur["api_key"]
    if key and not key.startswith("••••"):
        keys[preset] = key.strip()
    o["keys"] = keys
    o["api_key"] = keys.get(preset, "")
    return o


def public(cfg: dict) -> dict:
    """给页面看的配置：密钥只露后四位。"""
    out = copy.deepcopy(cfg)
    key = out["openai"].get("api_key") or ""
    out["openai"]["api_key"] = ("••••" + key[-4:]) if key else ""
    out["openai"]["has_key"] = bool(key)
    out["openai"]["saved_keys"] = [k for k, v in (out["openai"].pop("keys", None) or {}).items() if v]
    for model in (out.get("chat") or {}).get("models", []):
        model["has_key"] = bool(model.pop("api_key", ""))
    return out


def library_dir(cfg: dict | None = None) -> Path:
    p = Path((cfg or load())["library_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p

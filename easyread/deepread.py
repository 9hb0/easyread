"""论文精读文档：整理论文内容、调用已配置模型并保存 Markdown 结果。"""
from __future__ import annotations

import copy
import os
import re
import time
from pathlib import Path

from . import chat_models, engines
from .store import Workspace, dir_lock, now_iso, read_json, write_json_atomic

MAX_CHARS = 5000
DEFAULT_PROMPT = """请把这篇论文讲成一份适合认真阅读的中文精读文档：
- 先说清楚论文要解决的问题、核心想法和主要贡献；
- 再解释方法、关键公式或算法，以及实验如何验证结论；
- 最后总结结论、局限、适用场景和可复现要点；
- 尽量使用论文中的章节结构，必要时用 Markdown 标题和列表；
- 面向研究生读者，用人话解释，但不要牺牲准确性。"""

_STATE_DEFAULT = {
    "state": "idle", "message": "还没有生成精读", "updated": "", "model": "",
    "prompt": "", "chars": 0, "error": "",
}


def _state_path(ws: Workspace) -> Path:
    return ws.root / "deepread.json"


def _doc_path(ws: Workspace) -> Path:
    return ws.root / "deepread.md"


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, path)


def read(ws: Workspace) -> dict:
    state = copy.deepcopy(_STATE_DEFAULT)
    state.update(read_json(_state_path(ws), {}) or {})
    content = ""
    try:
        content = _doc_path(ws).read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    state["content"] = content
    state["has_document"] = bool(content)
    state["chars"] = len(content) if content else int(state.get("chars") or 0)
    return state


def set_state(ws: Workspace, **fields) -> dict:
    state = copy.deepcopy(_STATE_DEFAULT)
    state.update(read_json(_state_path(ws), {}) or {})
    state.update(fields)
    state["updated"] = now_iso()
    write_json_atomic(_state_path(ws), state)
    return read(ws)


def claim(ws: Workspace, model: str, prompt: str) -> tuple[dict, bool]:
    """原子地声明一次精读任务，返回 (状态, 是否由本次创建)。"""
    with dir_lock(ws.root):
        current = copy.deepcopy(_STATE_DEFAULT)
        current.update(read_json(_state_path(ws), {}) or {})
        if current["state"] in ("queued", "running"):
            return read(ws), False
        current.update(state="queued", message="排队中", model=model, prompt=prompt, error="")
        current["updated"] = now_iso()
        write_json_atomic(_state_path(ws), current)
        return read(ws), True


def _text(value) -> str:
    return str(value or "").strip()


def _block_text(block: dict) -> str:
    kind = block.get("type")
    if kind == "heading":
        level = max(1, min(3, int(block.get("level") or 1)))
        return "#" * level + " " + (_text(block.get("zh")) or _text(block.get("en")))
    if kind == "list":
        rows = []
        for i, item in enumerate(block.get("items") or [], 1):
            value = _text(item.get("zh")) or _text(item.get("en")) if isinstance(item, dict) else _text(item)
            if value:
                rows.append(f"{i}. {value}")
        return "\n".join(rows)
    if kind == "math":
        return "公式：" + (_text(block.get("tex")) or _text(block.get("en")))
    if kind == "table":
        caption = _text(block.get("caption_zh")) or _text(block.get("caption_en"))
        rows = ["表格：" + caption] if caption else []
        for row in [block.get("head") or [], *(block.get("rows") or [])]:
            rows.append(" | ".join(_text(cell) for cell in row))
        return "\n".join(x for x in rows if x)
    if kind == "figure":
        caption = _text(block.get("caption_zh")) or _text(block.get("caption_en"))
        return "图：" + caption if caption else "图（原文第 %s 页）" % (block.get("page") or "?")
    if kind == "references":
        return "参考文献：" + (_text(block.get("zh")) or _text(block.get("en")))
    return _text(block.get("zh")) or _text(block.get("en"))


def context(ws: Workspace) -> str:
    paper = ws.load("paper") or {}
    meta = paper.get("meta") or {}
    header = [
        "论文标题：" + (_text(meta.get("title_zh")) or _text(meta.get("title_en"))),
        "英文标题：" + _text(meta.get("title_en")),
        "作者：" + _text(meta.get("authors")),
        "出处：" + (_text(meta.get("venue")) or _text(meta.get("arxiv")) or _text(meta.get("doi"))),
    ]
    chunks = [x for x in header if x.strip("：")]
    block_pages = set()
    for block in paper.get("blocks") or []:
        if isinstance(block, dict):
            value = _block_text(block)
            if value:
                chunks.append(value)
            try:
                if block.get("page"):
                    block_pages.add(int(block["page"]))
            except (TypeError, ValueError):
                pass
    extract_dir = ws.root / "extract"
    for path in sorted(extract_dir.glob("page-*.txt")) if extract_dir.is_dir() else []:
        match = re.fullmatch(r"page-(\d+)\.txt", path.name)
        if not match or int(match.group(1)) in block_pages:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            chunks.append("原文第 " + str(int(match.group(1))) + " 页（未翻译）：\n" + text)
    return "\n\n".join(chunks)


def build_prompt(ws: Workspace, custom_prompt: str) -> str:
    instructions = _text(custom_prompt) or DEFAULT_PROMPT
    return instructions + """\n\n固定要求：
- 只根据下面的论文材料写作，不要编造论文没有的信息；
- 使用中文输出，公式、模型名和必要的英文术语可以保留；
- 输出不超过 5000 个字符；
- 直接输出精读正文，不要输出“好的”“以下是”等套话，也不要输出 JSON。

论文材料：
""" + context(ws)


def limit(text: str) -> str:
    return str(text or "").strip()[:MAX_CHARS]


def _engine_config(cfg: dict) -> tuple[dict, dict, str, str]:
    setting = cfg.get("deepread") or {}
    requested = _text(setting.get("model")) or (cfg.get("chat") or {}).get("default")
    engine_cfg, model = chat_models.engine_cfg(cfg, requested or None)
    prompt = _text(setting.get("prompt")) or DEFAULT_PROMPT
    return engine_cfg, model, model.get("id", requested or ""), prompt


def generate(ws: Workspace, cfg: dict, cancel=None) -> str:
    engine_cfg, model, model_id, custom_prompt = _engine_config(cfg)
    text = engines.run(engine_cfg, build_prompt(ws, custom_prompt), ws.root, [], cancel)
    result = limit(text)
    if not result:
        raise engines.EngineError("模型没有给出精读内容")
    _write_text_atomic(_doc_path(ws), result)
    set_state(ws, state="done", message="精读完成", model=model_id, prompt=custom_prompt, chars=len(result), error="")
    return result

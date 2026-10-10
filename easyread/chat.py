"""阅读页右侧的“问 AI”：提示词和流式输出（回答一个字一个字流出来）。

- 用哪个模型：chat_models.py（设置里的一张短名单）。
- 对话记录：chat_store.py（每篇论文可以有多个对话）。
- 上下文：整篇论文的原文（含未翻译页面）、参考文献、读者指着的段落和前后几段、术语表。
  读者的标记（按颜色分好的划线、笔记、问题）只在问题提到“标红的”“划线”“笔记”时才带上，
  提到具体颜色就只带那种颜色，所以可以问“我标红的那些公式之间有什么联系”。
  Claude Code 还能自己 Read paper.json、reader.json 看全文和全部标记；能看图的模型（设置里勾“模型能看图”）和 Codex 还会收到读者正在读的那几页原页图。
"""
from __future__ import annotations

import base64
import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from . import engines
from .prompts import _block_text
from .store import Workspace

HISTORY = 12  # 带上最近几轮对话
COLOR_NAMES = {"yellow": "黄", "green": "绿", "blue": "蓝", "pink": "红"}
MARKS_BUDGET = 9000  # 标记部分最多带多少字
VISION_PAGES = 4  # 问 AI 带图时最多附几页原页


# ---------- 提示词 ----------
def _source_text(block: dict) -> str:
    """用保存的原文 / 译文补齐抽取遗漏，保留列表、公式和表格数值。"""
    kind = block.get("type")
    if kind == "note":
        return "阅读批注（非原文）：" + (block.get("zh") or "")
    if kind == "list":
        return "\n".join("- " + (item.get("en") or item.get("zh") or "") for item in block.get("items", []))
    if kind == "math":
        return f"公式 {block.get('tag') or ''}：$${block.get('tex') or block.get('en') or ''}$$"
    if kind in ("table", "figure"):
        parts = [block.get("caption_en") or block.get("caption_zh") or ""]
        if kind == "table":
            head = block.get("head") or []
            if head and not isinstance(head[0], list):
                head = [head]
            parts += [" | ".join(str(cell) for cell in row) for row in [*head, *(block.get("rows") or [])]]
        return "\n".join(part for part in parts if part)
    text = block.get("en") or block.get("zh") or ""
    if kind == "heading":
        text = f"{block.get('num') or ''} {text}"
    return text.strip()


def _paper_context(ws: Workspace, paper: dict) -> str:
    """所有引擎都直接收到论文文本；原页文本优先，避免只翻译正文时漏掉文末引用。"""
    pages: dict[int, str] = {}
    for path in (ws.root / "extract").glob("page-*.txt"):
        match = re.fullmatch(r"page-(\d+)\.txt", path.name)
        if match:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                pages[int(match.group(1))] = text
    compact_pages = {page: "".join(text.split()) for page, text in pages.items()}
    supplemental: dict[int, list[str]] = {}
    for block in paper.get("blocks") or []:
        page = int(block.get("page") or 0)
        text = _source_text(block)
        if text and "".join(text.split()) not in compact_pages.get(page, ""):
            supplemental.setdefault(page, []).append(text)
    parts = []
    for page in sorted(pages.keys() | supplemental.keys()):
        label = f"第 {page} 页" if page else "未标页码的论文内容"
        if page in pages:
            parts.append(f"原文{label}（抽取文本，公式和表格可能排乱）：\n{pages[page]}")
        if page in supplemental:
            parts.append(f"{label}（保存内容补充）：\n" + "\n\n".join(supplemental[page]))
    references = [f"[{ref.get('id', '')}] {ref['text']}" for ref in paper.get("references") or [] if ref.get("text")]
    if references:
        parts.append("参考文献列表（编号与正文引用对应）：\n" + "\n".join(references))
    return "已载入的论文材料（按页排列，当前选中的段落只是提问重点）：\n\n" + "\n\n".join(parts) if parts else ""


def _context(ws: Workspace, anchor: str | None, quote: str, refs: list[dict] | None = None) -> str:
    paper = ws.load("paper")
    meta = paper.get("meta", {})
    blocks = paper.get("blocks", [])
    lines = [f"论文：《{meta.get('title_zh') or ''}》{meta.get('title_en') or ''}，{meta.get('authors', '')[:200]}。"]
    abstract = next((b.get("zh") for b in blocks if b.get("role") == "abstract"), "")
    if abstract:
        lines.append("摘要（译文）：" + abstract[:1500])
    idx = next((i for i, b in enumerate(blocks) if b.get("id") == anchor), None)
    if idx is not None:
        h = next((b for b in reversed(blocks[:idx + 1]) if b.get("type") == "heading"), None)
        if h:
            lines.append(f"读者正在读的章节：{h.get('num', '')} {h.get('zh', '')}")
        near = blocks[max(0, idx - 3): idx + 3]
        lines.append("附近的译文：\n" + "\n\n".join(f"[{b['id']}] {_block_text(b)}" for b in near))
        focus = blocks[idx]
        lines.append(f"读者指着的段落 [{focus['id']}]：\n译文：{_block_text(focus)}\n原文：{focus.get('en') or focus.get('caption_en') or focus.get('tex', '')}")
    if quote:
        lines.append(f"读者选中的原话：「{quote}」")
    extra = [r for r in (refs or []) if r.get("anchor") != anchor or (r.get("quote") or "") != quote]
    if extra:
        by_id = {b.get("id"): b for b in blocks}
        parts = []
        for r in extra[:12]:
            b = by_id.get(r.get("anchor")) or {}
            q = (r.get("quote") or "").strip()
            parts.append(f"[{r.get('anchor')}] " + (f"读者选中：「{q[:800]}」\n  所在段落：" if q else "") + _block_text(b)[:1200])
        lines.append("读者引用了这几处（问题可能是在问它们之间的关系）：\n" + "\n".join(parts))
    gl = paper.get("glossary", [])
    if gl:
        lines.append("术语表：" + "；".join(f"{g['en']} = {g['zh']}" for g in gl[:80]))
    full = _paper_context(ws, paper)
    if full:
        lines.append(full)
    return "\n\n".join(lines)


def _marks(ws: Workspace, colors: set[str] | None = None) -> str:
    """读者的全部标记，按颜色分组；每处带上所在段落的译文（含 $TeX$），这样问“红色那些公式”也答得上。"""
    paper = ws.load("paper")
    blocks = {b.get("id"): b for b in paper.get("blocks", [])}
    order = {b.get("id"): i for i, b in enumerate(paper.get("blocks", []))}
    notes = [n for n in (ws.load("reader").get("notes") or {}).values() if not n.get("deleted")]
    if not notes:
        return ""
    notes.sort(key=lambda n: order.get(n.get("anchor"), 1e9))
    kinds = {"highlight": "划线", "note": "笔记", "question": "问题"}
    groups: dict[str, list[str]] = {}
    shown: set[str] = set()
    for n in notes:
        color = COLOR_NAMES.get(n.get("color") or "yellow", "黄") if n.get("quote") else "无颜色"
        if colors and color not in colors:
            continue
        b = blocks.get(n.get("anchor")) or {}
        line = f"- [{n.get('anchor')}] {kinds.get(n.get('kind'), '笔记')}"
        if n.get("quote"):
            line += f"：「{n['quote']}」"
        if n.get("body"):
            line += f"；读者写道：{n['body'][:300]}"
        if b and b.get("id") not in shown:
            shown.add(b["id"])
            line += f"\n  所在段落：{_block_text(b)[:600]}"
        groups.setdefault(color, []).append(line)
    parts, used = [], 0
    for color in ["红", "黄", "绿", "蓝", "无颜色"]:
        for line in groups.get(color, []):
            if used > MARKS_BUDGET:
                break
            if not parts or not parts[-1].startswith(f"【{color}"):
                parts.append(f"【{color}色】" if color != "无颜色" else "【没有颜色的笔记和问题】")
            parts.append(line)
            used += len(line)
    return ("读者在译文上做的标记（按颜色分组，读者说“红的”“黄色那些”就是指这里；每处附所在段落译文，行内公式是 $TeX$）：\n"
            + "\n".join(parts) + ("\n（标记太多，只列了一部分；Claude Code 可以 Read reader.json 看全部）" if used > MARKS_BUDGET else ""))


MARK_WORDS = re.compile(r"标[红黄绿蓝记了过的出注]|划线|划过|划的|画线|高亮|涂|颜色|[红黄绿蓝][色的]|笔记|批注|标记|我的问题|highlight", re.I)


def wants_marks(text: str) -> tuple[bool, set[str] | None]:
    """问题里提到“标红的”“划线”“我的笔记”这类词，才把读者的标记带上；提到具体颜色就只带那几种。"""
    if not MARK_WORDS.search(text or ""):
        return False, None
    colors = {c for c in "红黄绿蓝" if re.search(c + "[色的]|标" + c, text)}
    return True, (colors | {"无颜色"} if colors and re.search(r"笔记|问题|批注", text) else colors or None)


def _marks_summary(ws: Workspace) -> str:
    notes = [n for n in (ws.load("reader").get("notes") or {}).values() if not n.get("deleted")]
    if not notes:
        return ""
    counts: dict[str, int] = {}
    for n in notes:
        k = COLOR_NAMES.get(n.get("color") or "yellow", "黄") + "色" if n.get("quote") else "无颜色笔记"
        counts[k] = counts.get(k, 0) + 1
    return "读者在论文上做过 " + str(len(notes)) + " 处标记（" + "、".join(f"{k} {v}" for k, v in counts.items()) + "），这次问题没提到，就没附上。"


def vision_pages(ws: Workspace, anchor: str | None, refs: list[dict] | None = None) -> list[int]:
    """读者指着 / 引用到的块在哪几页：问 AI 带图时把这些页的原页图发过去。"""
    ids = {anchor} | {r.get("anchor") for r in (refs or []) if r.get("anchor")}
    pages = sorted({b.get("page") for b in ws.load("paper").get("blocks", []) if b.get("id") in ids and b.get("page")})
    return pages[:VISION_PAGES]


def prompt(ws: Workspace, messages: list[dict], anchor: str | None, quote: str, engine: str, refs: list[dict] | None = None) -> str:
    history = messages[-HISTORY:]
    convo = "\n\n".join(("读者" if m["role"] == "user" else "你") + "：" + m["content"] for m in history[:-1])
    ask = history[-1]["content"] if history else ""
    tool = ("需要进一步核对时，用 Read 工具读当前目录的 paper.json（blocks 里是译文和原文，references 是参考文献）；"
            "未翻译页面的原文在 extract/page-*.txt；读者的全部标记在 reader.json 的 notes 里；"
            "要看图表、公式或原版式时，原页图按页码放在 extract/ 下（第 3 页是 page-003.jpg），可以用 Read 打开。\n"
            if engine == "claude" else "")
    want, colors = wants_marks(ask)
    marks = _marks(ws, colors) if want else _marks_summary(ws)
    return ("你在陪读者读一篇学术论文，回答他边读边冒出来的问题。用中文，直接、具体，能举例就举例；"
            "区分“论文里写了什么”和“你的补充解释”，论文里没有的内容不要说成是论文说的。"
            "下面的论文材料来自整篇论文，不限于当前页；问到其他章节、附录或引用编号时，先查这些材料和参考文献列表。"
            "之前的回答若说只能看到当前页，以本次附上的材料为准；材料确实缺失时再说明缺少什么。"
            "行内公式写 $TeX$，行间公式写 $$TeX$$。提到原文位置时说“式 5”“第 4 页那段”，不要写 [p4-5] 这类内部编号。只输出回答本身，不要客套，不要重复问题。\n" + tool + "\n"
            + _context(ws, anchor, quote, refs)
            + ("\n\n" + marks if marks else "")
            + (f"\n\n之前的对话：\n{convo}" if convo else "")
            + f"\n\n读者现在问：{ask}")


# ---------- 流式输出 ----------
def stream(ecfg: dict, text: str, cwd: Path, cancel: threading.Event, on_model=None, images: list[Path] | None = None) -> Iterator[str]:
    """on_model(实际模型名)：Claude Code 开头会报它实际用的模型。"""
    e = ecfg.get("engine")
    if e == "claude":
        yield from _stream_claude(ecfg["claude"], text, cwd, cancel, on_model)
    elif e == "openai":
        yield from _stream_openai(ecfg["openai"], text, cancel, images or [])
    else:  # codex 没有逐字输出，整段给
        yield engines.run(ecfg, text, cwd, images, cancel)


def _stream_claude(c: dict, text: str, cwd: Path, cancel, on_model=None) -> Iterator[str]:
    exe = engines.claude_path(c)
    if not exe:
        raise engines.EngineError("找不到 Claude Code 命令（先装好并登录 Claude Code）")
    args = [exe, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--allowedTools", "Read", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
    if c.get("model"):
        args += ["--model", c["model"]]
    if c.get("reasoning_effort"):
        args += ["--effort", c["reasoning_effort"]]
    proc = engines._popen(args, cwd)
    proc.stdin.write(text)
    proc.stdin.close()
    killer = threading.Thread(target=lambda: (cancel.wait(), proc.poll() is None and proc.kill()), daemon=True)
    killer.start()
    got = False
    try:
        for line in proc.stdout:
            if cancel.is_set():
                raise engines.Cancelled()
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init" and ev.get("model") and on_model:
                on_model(ev["model"])
            if ev.get("type") == "stream_event":
                d = (ev.get("event") or {}).get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    got = True
                    yield d["text"]
            elif ev.get("type") == "result":
                if ev.get("is_error"):
                    raise engines.EngineError("Claude Code 出错：" + str(ev.get("result") or ev.get("subtype")))
                if not got and ev.get("result"):
                    yield ev["result"]
                return
        err = proc.stderr.read()[-400:]
        if not got:
            raise engines.EngineError(err or "Claude Code 没有输出")
    finally:
        if proc.poll() is None:
            proc.kill()
        cancel.set()  # 让 killer 线程退出


def _stream_openai(o: dict, text: str, cancel, images: list[Path] | None = None) -> Iterator[str]:
    base = engines.openai_base(o.get("base_url") or "")
    if not base or not o.get("model"):
        raise engines.EngineError("API 没填地址或模型")
    content: str | list = text
    if o.get("vision") and images:
        content = [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()}}
            for p in images]
    body = {"model": o["model"], "temperature": 0.4, "stream": True, "messages": [{"role": "user", "content": content}]}
    if o.get("reasoning_effort"):
        body["reasoning_effort"] = o["reasoning_effort"]
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if o.get("api_key"):
        headers["Authorization"] = "Bearer " + o["api_key"]
    r = None
    for attempt in range(2):
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), headers=headers)
        try:
            r = urllib.request.urlopen(req, timeout=int(o.get("timeout") or 600))
            break
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            # 少数 OpenAI 中转站会把 reasoning_effort 转成它们不认识的
            # summary 字段；去掉推理参数重试一次，避免整段回答直接失败。
            if attempt == 0 and body.get("reasoning_effort") and e.code == 400 and "unknown field" in detail and "summary" in detail:
                body.pop("reasoning_effort", None)
                continue
            raise engines.EngineError(f"接口返回 {e.code}：{detail}")
        except Exception as e:  # noqa: BLE001
            raise engines.EngineError(f"连不上接口：{e}")
    thinking = False
    got = False
    with r:
        for raw in r:
            if cancel.is_set():
                raise engines.Cancelled()
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = (json.loads(data).get("choices") or [{}])[0].get("delta") or {}
            except json.JSONDecodeError:
                continue
            piece = delta.get("content") or ""
            # 推理模型把思考过程包在 <think> 里，读者不需要看
            if "<think>" in piece:
                thinking, piece = True, piece.split("<think>")[0]
            if thinking:
                if "</think>" not in piece:
                    continue
                thinking, piece = False, piece.split("</think>", 1)[1]
            if piece:
                got = True
                yield piece
    if not got:
        raise engines.EngineError("接口没有返回回答内容，请检查 BASE_URL、模型名，或中转站是否支持流式输出")

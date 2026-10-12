"""AI 精读预处理：六色语义高亮的校验与定位。

翻译批次里模型顺带返回的 highlights 由这里校验：
类别合法、quote 是该块最终译文的连续子串、occurrence 能唯一定位、彼此不重叠。
校验通过的才存进 paper.json，其余丢弃并报告问题（交给翻译修复流程重试一次）。
"""
from __future__ import annotations

from .store import text_hash

READING_VERSION = 1

# 顺序即前端筛选按钮顺序；颜色集中定义在 web/css/base.css 的 --sem-* 变量
CATEGORIES = {
    "background": "背景知识",
    "related_work": "相关工作",
    "problem": "解决的问题",
    "method": "提出的方法",
    "innovation": "核心创新",
    "evidence": "论据实验",
}


def block_zh(block: dict) -> str:
    """一个块参与高亮定位的中文文本（段落/标题/列表项/题注，公式和单元格不算）。"""
    t = block.get("type")
    if t in ("para", "heading", "references", "note"):
        return str(block.get("zh") or "")
    if t == "list":
        return "\n".join(str(it.get("zh") or "") for it in block.get("items") or [])
    if t in ("table", "figure"):
        return str(block.get("caption_zh") or "")
    return ""


def page_text(blocks: list, page: int) -> str:
    return "\n".join(t for b in blocks if b.get("page") == page if (t := block_zh(b)))


def page_hash(blocks: list, page: int) -> str:
    """页面译文版本的指纹：译文变了就能发现旧精读标注失效。"""
    return text_hash(page_text(blocks, page))


def locate_quote(text: str, quote: str, occurrence: int) -> tuple[int, int] | None:
    """quote 在 text 里第 occurrence 次出现（从 0 数）的区间；找不到返回 None。"""
    if not quote or occurrence is None or occurrence < 0:
        return None
    start = -1
    for _ in range(occurrence + 1):
        start = text.find(quote, start + 1)
        if start < 0:
            return None
    return (start, start + len(quote))


def validate_highlights(blocks: list[dict]) -> list[str]:
    """校验并就地规范化每个块的 highlights：只保留能唯一定位、互不重叠的。
    返回问题列表（给修复流程）。模型给的 id、颜色等自由字段一概不信，id 由这里确定性生成。"""
    problems: list[str] = []
    for b in blocks:
        raw = b.get("highlights")
        if raw is None:
            continue
        bid = b.get("id") or "?"
        if not isinstance(raw, list):
            problems.append(f"{bid}：highlights 不是数组")
            b.pop("highlights", None)
            continue
        text = block_zh(b)
        kept: list[dict] = []
        used: list[tuple[int, int]] = []
        for h in raw:
            if not isinstance(h, dict):
                problems.append(f"{bid}：高亮不是对象：{str(h)[:60]}")
                continue
            cat = h.get("category")
            quote = str(h.get("quote") or "")
            if cat not in CATEGORIES:
                problems.append(f"{bid}：高亮类别不合法：{str(cat)[:40]}")
                continue
            try:
                occ = int(h.get("occurrence") or 0)
            except (TypeError, ValueError):
                problems.append(f"{bid}：高亮 occurrence 不是整数")
                continue
            span = locate_quote(text, quote, occ) if text else None
            if span is None:
                problems.append(f"{bid}：高亮引文无法在该块译文中定位：{quote[:40]}")
                continue
            if any(span[0] < e and s < span[1] for s, e in used):
                problems.append(f"{bid}：高亮与其他高亮重叠：{quote[:40]}")
                continue
            used.append(span)
            kept.append({"id": f"h-{bid}-{len(kept)}", "category": cat, "quote": quote, "occurrence": occ})
        if kept:
            b["highlights"] = kept
        else:
            b.pop("highlights", None)
    return problems


def apply_reading_state(paper: dict, pages: list[int], status: str, enabled: bool) -> None:
    """翻译批次落库时更新页面级精读状态（在 paper.json 的同一次写里调用）。"""
    reading = paper.setdefault("reading", {})
    reading["version"] = READING_VERSION
    reading["enabled"] = enabled
    states = reading.setdefault("pages", {})
    blocks = paper.get("blocks", [])
    for p in pages:
        states[str(p)] = {"status": status, "zh_hash": page_hash(blocks, p)}

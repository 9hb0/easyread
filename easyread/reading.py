"""AI 精读预处理：六色语义高亮的校验与定位。

翻译批次里模型顺带返回的 highlights 由这里校验：
类别合法、quote 是该块最终译文的连续子串、occurrence 能唯一定位、彼此不重叠。
校验通过的才存进 paper.json，其余丢弃并报告问题（交给翻译修复流程重试一次）。
"""
from __future__ import annotations

import json

from .store import now_iso, text_hash

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

ANNOTATION_TYPES = {
    "concept": "概念",
    "method_explanation": "方法解释",
    "design_rationale": "设计动机",
    "formula": "公式",
    "experiment": "实验",
    "limitation": "局限",
    "question": "疑问",
}

BASES = {
    "author_statement": "作者陈述",
    "source_supported": "有原文依据",
    "ai_explanation": "AI 解释",
}

INSIGHT_TYPES = {
    "background": "背景",
    "related_work": "相关工作",
    "problem": "研究问题",
    "method": "方法",
    "innovation": "创新",
    "evidence": "证据",
    "limitation": "局限",
}

INSIGHT_STATUSES = ("provisional", "supported", "unsupported")
GUIDE_GROUPS = (("background", "background"), ("related_work", "related_work"), ("problems", "problem"),
                ("methods", "method"), ("innovations", "innovation"), ("evidence", "evidence"),
                ("limitations", "limitation"))


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


def validate_annotations(blocks: list[dict]) -> list[str]:
    """校验并就地规范化每个块的 annotations（精读批注）。
    anchor_quote 为空表示挂整块；不为空时必须能在该块译文中定位（首次出现）。
    模型给的 id 等自由字段一律不信，id 由这里确定性生成。"""
    problems: list[str] = []
    for b in blocks:
        raw = b.get("annotations")
        if raw is None:
            continue
        bid = b.get("id") or "?"
        if not isinstance(raw, list):
            problems.append(f"{bid}：annotations 不是数组")
            b.pop("annotations", None)
            continue
        text = block_zh(b)
        kept: list[dict] = []
        for a in raw:
            if not isinstance(a, dict):
                problems.append(f"{bid}：批注不是对象：{str(a)[:60]}")
                continue
            typ = a.get("type")
            if typ not in ANNOTATION_TYPES:
                problems.append(f"{bid}：批注类别不合法：{str(typ)[:40]}")
                continue
            content = str(a.get("content") or "").strip()
            if not content or len(content) > 600:
                problems.append(f"{bid}：批注内容为空或超长")
                continue
            basis = a.get("basis") or "ai_explanation"
            if basis not in BASES:
                problems.append(f"{bid}：批注依据不合法：{str(basis)[:40]}")
                continue
            quote = str(a.get("anchor_quote") or "")
            if quote and (not text or locate_quote(text, quote, 0) is None):
                problems.append(f"{bid}：批注锚点无法定位：{quote[:40]}")
                continue
            ann = {"id": f"a-{bid}-{len(kept)}", "type": typ, "content": content, "basis": basis}
            if quote:
                ann["anchor_quote"] = quote
            kept.append(ann)
        if kept:
            b["annotations"] = kept
        else:
            b.pop("annotations", None)
    return problems


def validate_reading(blocks: list[dict]) -> list[str]:
    """一次校验高亮 + 批注（翻译批次和补精读共用）。"""
    return validate_highlights(blocks) + validate_annotations(blocks)


def validate_insights(insights, blocks: list[dict]) -> tuple[list[dict], list[str]]:
    """校验模型返回的研究要点：类型合法、内容非空、block 必须是本批的块。
    返回 (合格列表, 问题列表)。id 在并入 paper 时按去重结果生成。"""
    out, problems = [], []
    if insights is None:
        return out, problems
    if not isinstance(insights, list):
        return out, ["insights 不是数组"]
    pages = {b.get("id"): b.get("page") for b in blocks}
    for it in insights:
        if not isinstance(it, dict):
            problems.append(f"研究要点不是对象：{str(it)[:60]}")
            continue
        typ = it.get("type")
        if typ not in INSIGHT_TYPES:
            problems.append(f"研究要点类型不合法：{str(typ)[:40]}")
            continue
        content = str(it.get("content") or "").strip()
        if not content or len(content) > 300:
            problems.append(f"研究要点内容为空或超长：{content[:40]}")
            continue
        block = str(it.get("block") or "")
        if block not in pages:
            problems.append(f"研究要点引用的块不在本批：{block}")
            continue
        status = it.get("status") or "provisional"
        if status not in INSIGHT_STATUSES:
            problems.append(f"研究要点状态不合法：{str(status)[:40]}")
            continue
        out.append({"type": typ, "content": content, "block": block, "page": pages[block], "status": status})
    return out, problems


def _norm_key(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isalnum())


def merge_insights(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """并入一批研究要点：同类型同内容（忽略空白和标点）的合并 source_refs，状态就高不就低。"""
    rank = {"unsupported": 0, "provisional": 1, "supported": 2}
    out = list(existing or [])
    index = {(_norm_key(i.get("content")), i.get("type")): i for i in out}
    for it in incoming or []:
        key = (_norm_key(it.get("content")), it.get("type"))
        hit = index.get(key)
        if hit:
            refs = hit.setdefault("source_refs", [{"page": hit.get("page"), "block": hit.get("block")}])
            ref = {"page": it.get("page"), "block": it.get("block")}
            if ref not in refs:
                refs.append(ref)
            if rank.get(it.get("status"), 1) > rank.get(hit.get("status"), 1):
                hit["status"] = it["status"]
            continue
        item = dict(it)
        item["id"] = ""
        item["source_refs"] = [{"page": it.get("page"), "block": it.get("block")}]
        out.append(item)
        index[key] = item
    for k, i in enumerate(out):
        if not i.get("id"):
            i["id"] = f"i{k + 1}-{text_hash(i['content'])[:6]}"
    return out


def drop_insights_on_pages(insights: list[dict], pages: list[int]) -> list[dict]:
    """重译某几页后，来源全部落在这些页上的旧研究要点作废。"""
    drop = set(pages)
    return [i for i in insights or []
            if not all(r.get("page") in drop for r in i.get("source_refs") or [{"page": i.get("page")}])]


def rebuild_guide(paper: dict) -> None:
    """把 insights 按类型重组成 reading_guide，更新覆盖范围和状态。纯程序，不调模型。"""
    reading = paper.setdefault("reading", {})
    insights = reading.get("insights") or []
    guide = paper.setdefault("reading_guide", {})
    guide["version"] = READING_VERSION
    for key, typ in GUIDE_GROUPS:
        guide[key] = [i["id"] for i in insights if i.get("type") == typ]
    done = sorted({int(p) for p in paper.get("translation", {}).get("done_pages", [])})
    states = reading.get("pages") or {}
    analyzed = [p for p in done if (states.get(str(p)) or {}).get("status") == "completed"]
    guide["coverage"] = {"analyzed": analyzed, "translated": done,
                         "total": paper.get("meta", {}).get("page_count") or 0}
    guide["status"] = "complete" if (analyzed and analyzed == done) else ("partial" if analyzed else "pending")


def insights_hash(paper: dict) -> str:
    insights = (paper.get("reading") or {}).get("insights") or []
    return text_hash(json.dumps(insights, ensure_ascii=False, sort_keys=True))


def apply_synthesis(paper: dict, summary: str, relations: list[dict], statuses: dict) -> None:
    """一次模型综合的结果写回：关系的两端必须是已有 insight，状态覆盖只认合法值。"""
    reading = paper.setdefault("reading", {})
    ids = {i.get("id") for i in reading.get("insights") or []}
    relations = [r for r in relations or []
                 if isinstance(r, dict) and r.get("from") in ids and r.get("to") in ids and str(r.get("content") or "").strip()]
    for i in reading.get("insights") or []:
        if i.get("id") in statuses:
            i["status"] = statuses[i["id"]]
    guide = paper.setdefault("reading_guide", {})
    if summary:
        guide["summary"] = summary
    guide["relations"] = relations
    guide["synth_of"] = insights_hash(paper)
    guide["updated"] = now_iso()


def reading_pages(blocks: list[dict]) -> set[int]:
    """有精读标注（高亮或批注）的页码集合。"""
    return {b.get("page") for b in blocks if b.get("highlights") or b.get("annotations")}


def pages_needing_reading(paper: dict, only_missing: bool = True) -> list[int]:
    """已翻译但缺精读的页：没记过状态、failed、stale；only_missing=False 时全部已译页都要。"""
    done = sorted({int(p) for p in paper.get("translation", {}).get("done_pages", [])})
    states = (paper.get("reading") or {}).get("pages") or {}
    out = []
    for p in done:
        status = (states.get(str(p)) or {}).get("status")
        if not status or status in ("failed", "stale", "pending"):
            out.append(p)
        elif not only_missing:
            out.append(p)
    return out


def apply_reading_state(paper: dict, pages: list[int], status, enabled: bool) -> None:
    """翻译批次落库时更新页面级精读状态（在 paper.json 的同一次写里调用）。
    status 可以是统一字符串，也可以是 {页码: 状态}。"""
    reading = paper.setdefault("reading", {})
    reading["version"] = READING_VERSION
    reading["enabled"] = enabled
    states = reading.setdefault("pages", {})
    blocks = paper.get("blocks", [])
    for p in pages:
        st = status.get(p, status) if isinstance(status, dict) else status
        hl = sum(len(b.get("highlights") or []) for b in blocks if b.get("page") == p)
        ann = sum(len(b.get("annotations") or []) for b in blocks if b.get("page") == p)
        states[str(p)] = {"status": st, "zh_hash": page_hash(blocks, p), "highlights": hl, "annotations": ann}

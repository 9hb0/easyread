"""AI 精读预处理：六色语义高亮的校验与定位。

翻译批次里模型顺带返回的 highlights 由这里校验：
类别合法、quote 是该块最终译文的连续子串、occurrence 能唯一定位、彼此不重叠。
校验通过的才存进 paper.json，其余丢弃并报告问题（交给翻译修复流程重试一次）。
"""
from __future__ import annotations

import copy
import json
import re

from .store import now_iso, text_hash

READING_VERSION = 2

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
RELATION_STATUSES = INSIGHT_STATUSES
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


def _cell_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value == 0:
            return "0"
        magnitude = abs(value)
        if value.is_integer() and magnitude < 1e21:
            return str(int(value))
        if 1e-6 <= magnitude < 1e21:
            return format(value, ".15f").rstrip("0").rstrip(".")
        mantissa, exponent = format(value, ".15e").split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return f"{mantissa}e{int(exponent):+d}"
    return str(value)


def page_content_hash(blocks: list, page: int) -> str:
    """Bind formulas, table values, block identities and English sources as well as prose."""
    records = []
    for block in blocks:
        if block.get("page") != page:
            continue
        fields = [_cell_string(block.get("id")), _cell_string(block.get("type")), block_zh(block),
                  _cell_string(block.get("tex")), _cell_string(block.get("tag")), _source_text(block)]
        if block.get("type") == "table":
            for row in (block.get("head") or []) + (block.get("rows") or []):
                fields.append("␟".join(_cell_string(cell) for cell in row))
        records.append("␟".join(fields))
    return text_hash("␞".join(records))


def locate_quote(text: str, quote: str, occurrence: int) -> tuple[int, int] | None:
    """quote 在 text 里第 occurrence 次出现（从 0 数）的区间；找不到返回 None。"""
    if not isinstance(text, str) or not isinstance(quote, str) or not quote or type(occurrence) is not int or occurrence < 0:
        return None
    start = -1
    for _ in range(occurrence + 1):
        start = text.find(quote, start + 1)
        if start < 0:
            return None
    return (start, start + len(quote))


def _plain_spans(block: dict) -> list[tuple[int, int]]:
    """可标注的原始字符区间；列表项和数学片段各自构成边界。"""
    texts = ([str(it.get("zh") or "") for it in block.get("items") or []]
             if block.get("type") == "list" else [block_zh(block)])
    spans, offset = [], 0
    for text in texts:
        cursor = 0
        while cursor < len(text):
            marker = re.search(r"(?<!\\)\$\$|(?<!\\)\$|\\\(|\\\[", text[cursor:])
            if marker is None:
                spans.append((offset + cursor, offset + len(text)))
                break
            start = cursor + marker.start()
            if start > cursor:
                spans.append((offset + cursor, offset + start))
            opening = marker.group()
            closing = {r"\(": r"\)", r"\[": r"\]"}.get(opening, opening)
            end = text.find(closing, start + len(opening))
            while end > 0 and closing.startswith("$") and text[end - 1] == "\\":
                end = text.find(closing, end + len(closing))
            cursor = len(text) if end < 0 else end + len(closing)
        offset += len(text) + 1
    return spans


def _locate_in_block(block: dict, quote: str, occurrence: int) -> tuple[int, int] | None:
    span = locate_quote(block_zh(block), quote, occurrence)
    return span if span and any(s <= span[0] and span[1] <= e for s, e in _plain_spans(block)) else None


def _source_text(block: dict) -> str:
    """优先使用 normalizer 保存的原文字串，避免信任模型改写的 en。"""
    if "source_en" in block:
        return block["source_en"] if isinstance(block["source_en"], str) else ""
    if block.get("type") == "list":
        return "\n".join(str(it.get("en") or "") for it in block.get("items") or [])
    key = "caption_en" if block.get("type") in ("table", "figure") else "en"
    return block.get(key) if isinstance(block.get(key), str) else ""


def _unique_refs(refs: list[dict]) -> list[dict]:
    out = []
    for ref in refs:
        if ref not in out:
            out.append(copy.deepcopy(ref))
    return out


def validate_source_refs(refs, blocks: list[dict], default_block: dict | None = None) -> tuple[list[dict], list[str]]:
    """校验实际页面、块及英文连续短引文。旧数据可保留所属块的定位引用。"""
    if refs is None:
        return [], []
    if not isinstance(refs, list):
        return [], ["source_refs 不是数组"]
    known = {b.get("id"): b for b in blocks if isinstance(b.get("id"), str)}
    out, problems = [], []
    for ref in refs:
        if not isinstance(ref, dict):
            problems.append("来源引用不是对象")
            continue
        bid = ref.get("block", ref.get("block_id"))
        page = ref.get("page")
        target = known.get(bid) if isinstance(bid, str) else None
        if not target or type(page) is not int or page <= 0 or target.get("page") != page:
            problems.append(f"来源引用的页码或块不存在：{str(bid)[:40]}")
            continue
        if "block_id" in ref and ref["block_id"] != bid:
            problems.append(f"来源引用的块不一致：{bid}")
            continue
        normalized = {"page": page, "block": bid}
        quotes = [key for key in ("source_quote", "quote") if key in ref]
        if not quotes:
            if default_block and default_block.get("id") == bid and default_block.get("page") == page:
                out.append(normalized)
            else:
                problems.append(f"来源引用缺少英文引文：{bid}")
            continue
        source = _source_text(target)
        if any(not isinstance(ref[key], str) or not ref[key].strip() or len(ref[key]) > 300 or ref[key] not in source
               for key in quotes):
            problems.append(f"来源引文无法在英文原文中定位：{bid}")
            continue
        if len(quotes) == 2 and ref["source_quote"] != ref["quote"]:
            problems.append(f"来源引用的两条引文不一致：{bid}")
            continue
        normalized.update({key: ref[key] for key in quotes})
        out.append(normalized)
    return _unique_refs(out), problems


def _has_evidence(refs: list[dict]) -> bool:
    return any(type(ref.get("page")) is int and isinstance(ref.get("block"), str)
               and any(isinstance(ref.get(k), str) and ref[k].strip() for k in ("source_quote", "quote"))
               for ref in refs)


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
        candidates: list[tuple[dict, tuple[int, int]]] = []
        for h in raw:
            if not isinstance(h, dict):
                problems.append(f"{bid}：高亮不是对象：{str(h)[:60]}")
                continue
            cat = h.get("category")
            quote = h.get("quote")
            if not isinstance(b.get("id"), str) or not b["id"] or h.get("block_id", b["id"]) != b["id"]:
                problems.append(f"{bid}：高亮 block_id 与所属块不一致")
                continue
            if not isinstance(cat, str) or cat not in CATEGORIES:
                problems.append(f"{bid}：高亮类别不合法：{str(cat)[:40]}")
                continue
            occ = h.get("occurrence", 0)
            if type(occ) is not int:
                problems.append(f"{bid}：高亮 occurrence 不是整数")
                continue
            span = _locate_in_block(b, quote, occ)
            if span is None:
                problems.append(f"{bid}：高亮引文无法在兼容的译文片段中定位：{str(quote)[:40]}")
                continue
            if any(span == previous and cat == item["category"] for item, previous in candidates):
                continue
            candidates.append(({"block_id": bid, "category": cat, "quote": quote, "occurrence": occ}, span))
        conflicts = set()
        for k, (_, (s, e)) in enumerate(candidates):
            for j in range(k):
                a, z = candidates[j][1]
                if s < z and a < e:
                    conflicts.update((j, k))
        kept = []
        for k, (item, _) in enumerate(candidates):
            if k in conflicts:
                problems.append(f"{bid}：高亮与其他高亮重叠：{item['quote'][:40]}")
                continue
            kept.append({"id": f"h-{bid}-{len(kept)}", **item})
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
        kept: list[dict] = []
        for a in raw:
            if not isinstance(a, dict):
                problems.append(f"{bid}：批注不是对象：{str(a)[:60]}")
                continue
            typ = a.get("type")
            if not isinstance(b.get("id"), str) or not b["id"] or a.get("block_id", b["id"]) != b["id"]:
                problems.append(f"{bid}：批注 block_id 与所属块不一致")
                continue
            if not isinstance(typ, str) or typ not in ANNOTATION_TYPES:
                problems.append(f"{bid}：批注类别不合法：{str(typ)[:40]}")
                continue
            content = a.get("content")
            content = content.strip() if isinstance(content, str) else ""
            if not content or len(content) > 600:
                problems.append(f"{bid}：批注内容为空或超长")
                continue
            basis = a.get("basis") or "ai_explanation"
            if not isinstance(basis, str) or basis not in BASES:
                problems.append(f"{bid}：批注依据不合法：{str(basis)[:40]}")
                continue
            quote = a.get("anchor_quote", "")
            occ = a.get("occurrence", 0)
            if type(occ) is not int or occ < 0 or not isinstance(quote, str) or (not quote and occ != 0):
                problems.append(f"{bid}：批注锚点或 occurrence 不合法")
                continue
            if quote and _locate_in_block(b, quote, occ) is None:
                problems.append(f"{bid}：批注锚点无法在兼容片段中定位：{quote[:40]}")
                continue
            raw_refs = a.get("source_refs")
            if raw_refs is None or raw_refs == []:
                raw_refs = [{"page": b.get("page"), "block": bid}]
            refs, issues = validate_source_refs(raw_refs, blocks, default_block=b)
            if issues or not refs:
                problems.extend(f"{bid}：{issue}" for issue in issues or ["批注缺少来源引用"])
                continue
            ann = {"id": f"a-{bid}-{len(kept)}", "block_id": bid, "type": typ, "content": content,
                   "basis": basis, "occurrence": occ, "source_refs": refs}
            if quote:
                ann["anchor_quote"] = quote
            if any({k: v for k, v in item.items() if k != "id"} == {k: v for k, v in ann.items() if k != "id"}
                   for item in kept):
                continue
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
    known = {b.get("id"): b for b in blocks if isinstance(b.get("id"), str)}
    for it in insights:
        if not isinstance(it, dict):
            problems.append(f"研究要点不是对象：{str(it)[:60]}")
            continue
        typ = it.get("type")
        if not isinstance(typ, str) or typ not in INSIGHT_TYPES:
            problems.append(f"研究要点类型不合法：{str(typ)[:40]}")
            continue
        content = it.get("content")
        content = content.strip() if isinstance(content, str) else ""
        if not content or len(content) > 300:
            problems.append(f"研究要点内容为空或超长：{content[:40]}")
            continue
        bid = it.get("block", it.get("block_id"))
        owner = known.get(bid) if isinstance(bid, str) else None
        if bid is not None and not owner:
            problems.append(f"研究要点引用的块不在本批：{str(bid)[:40]}")
            continue
        status = it.get("status") or "provisional"
        if status not in INSIGHT_STATUSES:
            problems.append(f"研究要点状态不合法：{str(status)[:40]}")
            continue
        raw_refs = it.get("source_refs")
        if (raw_refs is None or raw_refs == []) and owner:
            raw_refs = [{"page": owner.get("page"), "block": bid}]
        refs, issues = validate_source_refs(raw_refs, blocks, default_block=owner)
        if issues or not refs:
            problems.extend(issues or ["研究要点缺少有效来源引用"])
            continue
        if not owner:
            bid = refs[0]["block"]
            owner = known[bid]
        if "page" in it and it["page"] != owner.get("page"):
            problems.append(f"研究要点的页码与所属块不一致：{bid}")
            continue
        if status == "supported" and not _has_evidence(refs):
            status = "provisional"
        out.append({"type": typ, "content": content, "block": bid, "page": owner.get("page"),
                    "status": status, "source_refs": refs})
    return out, problems


def _norm_key(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isalnum())


def merge_insights(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """幂等合并已校验要点，保留每条精确引用；没有引文不能晋升 supported。"""
    rank = {"unsupported": 0, "provisional": 1, "supported": 2}
    out, index = [], {}
    for raw in list(existing or []) + list(incoming or []):
        it = copy.deepcopy(raw)
        refs = it.get("source_refs")
        if refs is None:
            refs = [{"page": it.get("page"), "block": it.get("block")}]
        refs = _unique_refs([r for r in refs or [] if isinstance(r, dict)])
        it["source_refs"] = refs
        if it.get("status") not in INSIGHT_STATUSES or (it.get("status") == "supported" and not _has_evidence(refs)):
            it["status"] = "provisional"
        key = (_norm_key(it.get("content")), it.get("type"))
        hit = index.get(key)
        if hit:
            hit["source_refs"] = _unique_refs(hit["source_refs"] + refs)
            if rank.get(it.get("status"), 1) > rank.get(hit.get("status"), 1):
                hit["status"] = it["status"]
            continue
        out.append(it)
        index[key] = it
    used_ids = {i.get("id") for i in out if i.get("id")}
    for k, i in enumerate(out):
        if not i.get("id"):
            serial = k + 1
            iid = f"i{serial}-{text_hash(i['content'])[:6]}"
            while iid in used_ids:
                serial += 1
                iid = f"i{serial}-{text_hash(i['content'])[:6]}"
            i["id"] = iid
            used_ids.add(iid)
    return out


def drop_insights_on_pages(insights: list[dict], pages: list[int]) -> list[dict]:
    """移除受影响页的每条引用，跨页要点重新定位并降回候选状态。"""
    drop = set(pages)
    out = []
    for raw in insights or []:
        refs = raw.get("source_refs") or [{"page": raw.get("page"), "block": raw.get("block")}]
        kept = [r for r in refs if r.get("page") not in drop]
        if not kept:
            continue
        item = copy.deepcopy(raw)
        item["source_refs"] = _unique_refs(kept)
        if len(kept) != len(refs):
            item["page"], item["block"] = kept[0].get("page"), kept[0].get("block")
            if item.get("status") == "supported":
                item["status"] = "provisional"
        out.append(item)
    return out


def _pages(values) -> list[int]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({int(p) for p in values if type(p) is int and p > 0
                   or isinstance(p, str) and p.isdigit() and int(p) > 0})


def _state_current(paper: dict, page: int) -> bool:
    state = ((paper.get("reading") or {}).get("pages") or {}).get(str(page)) or {}
    source = ((paper.get("translation") or {}).get("source_versions") or {}).get(str(page))
    return (state.get("status") == "completed" and state.get("version") == READING_VERSION
            and state.get("zh_hash") == page_hash(paper.get("blocks") or [], page)
            and state.get("content_hash") == page_content_hash(paper.get("blocks") or [], page)
            and "source_version" in state and state["source_version"] == source)


def invalidate_synthesis(paper: dict) -> None:
    """来源或要点变动后，清除旧综合中的结论及核验引用。"""
    guide = paper.get("reading_guide") or {}
    for key in ("summary", "summary_refs", "relations", "checks", "synth_of", "updated"):
        guide.pop(key, None)


def rebuild_guide(paper: dict) -> None:
    """把 insights 按类型重组成 reading_guide，更新覆盖范围和状态。纯程序，不调模型。"""
    reading = paper.setdefault("reading", {})
    insights = reading.get("insights") or []
    guide = paper.setdefault("reading_guide", {})
    if guide.get("synth_of") != insights_hash(paper):
        invalidate_synthesis(paper)
    guide["version"] = READING_VERSION
    for key, typ in GUIDE_GROUPS:
        guide[key] = [i["id"] for i in insights if i.get("type") == typ and i.get("id")]
    meta, tr = paper.get("meta") or {}, paper.get("translation") or {}
    count = meta.get("page_count")
    metadata_pages = _pages([p.get("n") for p in meta.get("pages") or [] if isinstance(p, dict)])
    total = max(count if type(count) is int and count > 0 else 0, max(metadata_pages, default=0))
    full = set(range(1, total + 1))
    done = _pages(tr.get("done_pages"))
    analyzed = [p for p in done if (not total or p in full) and _state_current(paper, p)]
    requested = tr.get("requested_scope") or ("pages" if tr.get("target_pages") else "all")
    is_full = requested in ("all", "全文", "full")
    target = sorted(full) if is_full else _pages(tr.get("target_pages")) or sorted(full)
    full_complete = bool(total and full.issubset(analyzed))
    range_complete = bool(target and set(target).issubset(analyzed))
    complete = full_complete if is_full else range_complete
    guide["coverage"] = {"analyzed": analyzed, "translated": done,
                         "total": total, "target_pages": target, "excluded_pages": sorted(full - set(target)),
                         "scope": requested, "range_complete": range_complete, "full_complete": full_complete}
    guide["status"] = "complete" if complete else ("partial" if analyzed else "pending")


def insights_hash(paper: dict) -> str:
    insights = (paper.get("reading") or {}).get("insights") or []
    return text_hash(json.dumps(insights, ensure_ascii=False, sort_keys=True))


def apply_synthesis(paper: dict, summary: str, relations: list[dict], statuses,
                    summary_refs=None, expected_hash=None) -> dict:
    """保存有原文引用的综合；校验只证明引文可定位，不代表科学主张已获证明。"""
    if expected_hash is not None and expected_hash != insights_hash(paper):
        return {"stale": True}
    insights = (paper.get("reading") or {}).get("insights") or []
    by_id = {i["id"]: i for i in insights if isinstance(i.get("id"), str) and i["id"]}
    blocks = paper.get("blocks") or []
    known_blocks = {b.get("id"): b for b in blocks if isinstance(b.get("id"), str)}
    endpoint_sources = {}
    for iid, item in by_id.items():
        owner = known_blocks.get(item.get("block")) if isinstance(item.get("block"), str) else None
        raw_refs = item.get("source_refs")
        if not raw_refs and owner:
            raw_refs = [{"page": owner.get("page"), "block": owner["id"]}]
        refs, _ = validate_source_refs(raw_refs, blocks, default_block=owner)
        endpoint_sources[iid] = {(r["page"], r["block"]) for r in refs}
    if isinstance(statuses, dict):
        checks = statuses.get("checks") if isinstance(statuses.get("checks"), list) else [
            {"insight": iid, "status": status} if not isinstance(status, dict) else {**status, "insight": iid}
            for iid, status in statuses.items()]
    else:
        checks = statuses if isinstance(statuses, list) else []
    accepted_checks = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        iid, status = check.get("insight"), check.get("status")
        item = by_id.get(iid) if isinstance(iid, str) else None
        if not item or status not in INSIGHT_STATUSES:
            continue
        refs, issues = validate_source_refs(check.get("source_refs", item.get("source_refs")), blocks)
        if status == "supported" and (issues or not _has_evidence(refs)):
            continue
        if "source_refs" in check and issues:
            continue
        item["status"] = status
        if refs:
            item["source_refs"] = refs
        accepted_checks.append({"insight": iid, "status": status, "source_refs": refs})
    accepted_relations = []
    for relation in relations if isinstance(relations, list) else []:
        if not isinstance(relation, dict):
            continue
        start, end, content = relation.get("from"), relation.get("to"), relation.get("content")
        status = relation.get("status", "provisional")
        if (not isinstance(start, str) or not isinstance(end, str) or start not in by_id or end not in by_id
                or start == end or not isinstance(content, str) or not content.strip() or status not in RELATION_STATUSES):
            continue
        refs, issues = validate_source_refs(relation.get("source_refs"), blocks)
        if issues or not _has_evidence(refs):
            continue
        cited_sources = {(r["page"], r["block"]) for r in refs}
        if not all(cited_sources & endpoint_sources[iid] for iid in (start, end)):
            continue
        accepted_relations.append({"from": start, "to": end, "content": content.strip(),
                                   "status": status, "source_refs": refs})
    refs, issues = validate_source_refs(summary_refs, blocks)
    valid_summary = isinstance(summary, str) and bool(summary.strip()) and not issues and _has_evidence(refs)
    rebuild_guide(paper)
    guide = paper["reading_guide"]
    if valid_summary:
        guide["summary"], guide["summary_refs"] = summary.strip(), refs
    elif summary:
        guide.pop("summary", None)
        guide.pop("summary_refs", None)
    guide["relations"] = _unique_refs(accepted_relations)
    guide["checks"] = accepted_checks
    if valid_summary or accepted_relations or accepted_checks:
        guide["synth_of"] = insights_hash(paper)
        guide["updated"] = now_iso()
    else:
        guide.pop("synth_of", None)
        guide.pop("updated", None)
    return {"stale": False}


def reading_pages(blocks: list[dict]) -> set[int]:
    """有精读标注（高亮或批注）的页码集合。"""
    return {b.get("page") for b in blocks if b.get("highlights") or b.get("annotations")}


def pages_needing_reading(paper: dict, only_missing: bool = True) -> list[int]:
    """已翻译但缺精读的页：没记过状态、failed、stale；only_missing=False 时全部已译页都要。"""
    done = _pages((paper.get("translation") or {}).get("done_pages"))
    return [p for p in done if not only_missing or not _state_current(paper, p)]


def apply_reading_state(paper: dict, pages: list[int], status, enabled: bool) -> None:
    """翻译批次落库时更新页面级精读状态（在 paper.json 的同一次写里调用）。
    status 可以是统一字符串，也可以是 {页码: 状态}。"""
    reading = paper.setdefault("reading", {})
    reading["version"] = READING_VERSION
    reading["enabled"] = enabled
    states = reading.setdefault("pages", {})
    blocks = paper.get("blocks", [])
    for p in pages:
        st = status.get(p, status.get(str(p), "pending")) if isinstance(status, dict) else status
        if st not in ("pending", "completed", "failed", "skipped", "stale"):
            st = "pending"
        hl = sum(len(b.get("highlights") or []) for b in blocks if b.get("page") == p)
        ann = sum(len(b.get("annotations") or []) for b in blocks if b.get("page") == p)
        source_version = ((paper.get("translation") or {}).get("source_versions") or {}).get(str(p))
        states[str(p)] = {"status": st, "version": READING_VERSION, "zh_hash": page_hash(blocks, p),
                          "content_hash": page_content_hash(blocks, p),
                          "source_version": source_version, "highlights": hl, "annotations": ann}

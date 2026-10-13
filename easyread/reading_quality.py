"""Rebuildable highlight quality data. Never edits source text or candidates.

Only whitespace-normalized exact repeats of sufficiently complete quotes are
suppressed. Similarity alone cannot establish semantic equivalence.
"""
from __future__ import annotations

import copy
import json
import re

from . import reading
from .store import text_hash

QUALITY_VERSION = 1


def highlight_stable_key(block_id: str, highlight: dict) -> str:
    return "|".join((block_id, str(highlight.get("category") or ""),
                     str(highlight.get("occurrence", 0)), text_hash(highlight.get("quote") or "")))


def quality_input_hash(paper: dict) -> str:
    """FNV/UTF-16 over compact arrays, shared with the native reader.

Bind all candidates and page dependencies, even invalid pages, so stale quality
can never hide a different set of highlights after an edit or re-analysis.
"""
    blocks = paper.get("blocks") or []
    tr, rd = paper.get("translation") or {}, paper.get("reading") or {}
    done = tr.get("done_pages") or []
    pages = sorted({b["page"] for b in blocks if type(b.get("page")) is int}
                   | {p for p in done if type(p) is int})
    states, versions = rd.get("pages") or {}, tr.get("source_versions") or {}
    page_rows = []
    for page in pages:
        st = states.get(str(page)) or {}
        page_rows.append([page, versions.get(str(page)), st.get("status"), st.get("version"),
                          st.get("zh_hash"), st.get("content_hash"), st.get("source_version"),
                          st.get("prompt_version"), reading.page_hash(blocks, page),
                          reading.page_content_hash(blocks, page)])
    candidate_rows = []
    for block in blocks:
        for h in block.get("highlights") or []:
            if not isinstance(h, dict):
                continue
            candidate_rows.append([block.get("id"), block.get("page"),
                                   highlight_stable_key(block.get("id") or "", h),
                                   h.get("category"), h.get("quote"), h.get("occurrence", 0),
                                   reading.normalize_importance(h.get("importance")),
                                   block.get("type"), block.get("role"),
                                   h.get("block_id"),
                                   [True, h["source_refs"]] if "source_refs" in h else [False, None]])
    payload = [reading.READING_VERSION, reading.PROMPT_VERSION, QUALITY_VERSION,
               rd.get("version"), done, page_rows, candidate_rows]
    return text_hash(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def rebuild_quality(paper: dict) -> None:
    """Store a deterministic cache; comparisons only include current analyzed pages."""
    blocks = paper.get("blocks") or []
    done = set((paper.get("translation") or {}).get("done_pages") or [])
    versions = (paper.get("translation") or {}).get("source_versions") or {}
    current = {p for p in done if type(p) is int and reading._state_current(paper, p)
               and isinstance(versions.get(str(p)), str) and versions[str(p)]}
    groups = {}
    for block in blocks:
        if block.get("page") not in current or not reading._source_text(block).strip():
            continue
        checked = copy.deepcopy(block)
        reading.validate_highlights([checked])
        allowed = {highlight_stable_key(block["id"], h) for h in checked.get("highlights") or []}
        seen = set()
        for h in block.get("highlights") or []:
            if (not isinstance(h, dict) or h.get("block_id") != block.get("id")
                    or not isinstance(h.get("category"), str) or h["category"] not in reading.CATEGORIES):
                continue
            key = highlight_stable_key(block["id"], h)
            if key not in allowed or key in seen:
                continue
            seen.add(key)
            if "source_refs" in h:
                refs, problems = reading.validate_source_refs(h["source_refs"], blocks, default_block=block)
                if problems or not refs or any(r["page"] not in current for r in refs):
                    continue
            quote = h.get("quote")
            occurrence = h.get("occurrence", 0)
            if reading._locate_in_block(block, quote, occurrence) is None:
                continue
            normalized = re.sub(r"\s+", "", quote)
            if len(normalized) < 12:
                continue
            identity = (h["category"], normalized)
            score = reading.normalize_importance(h.get("importance")) or 0
            body = block.get("type") == "para" and block.get("role") != "abstract"
            groups.setdefault(identity, []).append((key, score, body))
    suppressed = []
    for entries in groups.values():
        if len(entries) < 2:
            continue
        canonical = max(entries, key=lambda row: (row[1], row[2]))[0]  # stable document order breaks ties
        for key, _, _ in entries:
            if key != canonical:
                suppressed.append({"key": key, "canonical_key": canonical, "reason": "exact_duplicate"})
    paper.setdefault("reading", {})["quality"] = {
        "version": QUALITY_VERSION, "input_hash": quality_input_hash(paper), "suppressed": suppressed,
    }

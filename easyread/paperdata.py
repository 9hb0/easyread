"""翻译方对 paper.json / discussion.json 的写入：并入译文块、追加讨论。都在锁内完成。"""
from __future__ import annotations

import hashlib
import json

from .store import Workspace, now_iso
from . import reading as reading_mod

BLOCK_TYPES = {"heading", "para", "list", "math", "table", "figure", "references", "note"}
DISCUSSION_KINDS = {"explain", "qa", "insight", "reply", "check"}


def parse_pages(spec) -> list[int]:
    if isinstance(spec, list):
        return [int(x) for x in spec]
    out = []
    for part in str(spec or "").split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def merge_blocks(ws: Workspace, data: dict, done=None, replace_pages=None, reading: dict | None = None) -> dict:
    """并入一批块。同 id 整块替换；新块按 _after 或页码顺序插入。
    replace_pages：先删掉这些页上已有的块（重新翻译某几页时用）。"""
    if isinstance(data, list):
        data = {"blocks": data}
    for b in data.get("blocks", []):
        if not b.get("id") or b.get("type") not in BLOCK_TYPES:
            raise ValueError(f"块缺 id 或类型不对：{str(b)[:120]}")

    def apply(paper):
        blocks = paper.setdefault("blocks", [])
        if replace_pages:
            drop = set(replace_pages)
            blocks[:] = [b for b in blocks if b.get("page") not in drop]
        n_new = n_upd = 0
        last = None  # 同一批的新块保持给定顺序，接在上一个新块后面
        for b in data.get("blocks", []):
            b = dict(b)
            after = b.pop("_after", None)
            index = {x["id"]: i for i, x in enumerate(blocks)}
            if b["id"] in index:
                blocks[index[b["id"]]] = b
                n_upd += 1
                continue
            if after in index:
                pos = index[after] + 1
            elif last is not None:
                pos = index[last] + 1
            else:  # 这批第一个新块按页码放：插在第一个页码更大的块之前
                pos = next((i for i, x in enumerate(blocks) if (x.get("page") or 0) > (b.get("page") or 0)), len(blocks))
            blocks.insert(pos, b)
            last = b["id"]
            n_new += 1
        for key, ident in (("glossary", "en"), ("references", "id")):
            if data.get(key):
                have = {str(x.get(ident)) for x in paper.get(key, [])}
                paper[key] = paper.get(key, []) + [x for x in data[key] if str(x.get(ident)) not in have]
        if data.get("meta"):
            meta = paper.setdefault("meta", {})
            for k, v in data["meta"].items():
                if v and k not in ("pages", "source_sha256", "pdf", "page_count"):
                    meta[k] = v
        tr = paper.setdefault("translation", {})
        if data.get("translation"):
            tr.update(data["translation"])
        if reading:
            reading_mod.apply_reading_state(paper, parse_pages(reading.get("pages") or []),
                                             reading.get("status") or "completed", bool(reading.get("enabled", True)))
        if done:
            tr["done_pages"] = sorted(set(tr.get("done_pages", [])) | set(parse_pages(done)))
            total = paper.get("meta", {}).get("page_count") or 0
            n = len(tr["done_pages"])
            tr["scope"] = "全文" if total and n >= total else f"已译 {n} / {total} 页"
        return {"new": n_new, "updated": n_upd, "done_pages": tr.get("done_pages", [])}

    return ws.update("paper", apply)


def add_discussion(ws: Workspace, items) -> tuple[int, int]:
    items = items if isinstance(items, list) else [items]
    paper = ws.load("paper")
    block_ids = {b.get("id") for b in paper.get("blocks", [])}
    notes = ws.load("reader").get("notes", {})
    for it in items:
        if it.get("kind", "explain") not in DISCUSSION_KINDS:
            raise ValueError(f"kind 只能是 {sorted(DISCUSSION_KINDS)}")
        if it.get("anchor") and it["anchor"] not in block_ids:
            raise ValueError(f"锚点块不存在：{it['anchor']}")
        if it.get("reply_to") and it["reply_to"] not in notes:
            raise ValueError(f"要回复的用户笔记不存在：{it['reply_to']}")
        if not (it.get("body") or "").strip():
            raise ValueError("body 不能为空")

    def merge(disc):
        entries = disc.setdefault("entries", [])
        by_id = {e["id"]: e for e in entries}
        stamp = now_iso()
        n_new = n_upd = 0
        for k, it in enumerate(items):
            it = dict(it)
            it.setdefault("kind", "explain")
            if it.get("id") in by_id:
                by_id[it["id"]].update(it)
                by_id[it["id"]]["updated"] = stamp
                n_upd += 1
            else:
                it.setdefault("id", f"d{len(entries) + 1:03d}-{hashlib.md5((stamp + str(k)).encode()).hexdigest()[:5]}")
                it["at"] = stamp
                entries.append(it)
                n_new += 1
        return n_new, n_upd

    return ws.update("discussion", merge)


def delete_discussion(ws: Workspace, did: str) -> int:
    def drop(disc):
        before = len(disc["entries"])
        disc["entries"] = [e for e in disc["entries"] if e.get("id") != did]
        return before - len(disc["entries"])
    return ws.update("discussion", drop)


def set_block_text(ws: Workspace, key: str, zh: str) -> None:
    """重译一段后写回译者稿。key 同页面：块 id、id#caption、id#序号。译文变了，这块的旧高亮作废。"""
    bid, _, field = key.partition("#")

    def apply(paper):
        for b in paper.get("blocks", []):
            if b.get("id") != bid:
                continue
            if field == "caption":
                b["caption_zh"] = zh
            elif field.isdigit():
                b["items"][int(field)]["zh"] = zh
            else:
                b["zh"] = zh
            b.pop("highlights", None)
            return
        raise KeyError(key)
    ws.update("paper", apply)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)

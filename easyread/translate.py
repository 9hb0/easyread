"""后台翻译：一批几页交给模型，校验后并进 paper.json。每批落盘，中断了下次接着译。

一批失败会重试；还是失败就记下来跳过，接着译后面的页，最后在页面上给“重试失败的页”。
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import engines, pdfwork, prompts, reading, reading_quality, sources, sourceblocks
from .checks import block_problems, tex_problems
from .log import log
from .paperdata import add_discussion, merge_blocks, set_block_text
from .store import Workspace, now_iso

_merge_lock = threading.Lock()  # 并发翻译时，并入 paper.json 和重算原页定位一次只做一个
# 用量到顶、余额不足这类错误，后面的批次也一定失败：直接停，剩下的页记为没译，等额度恢复后一键重试
_QUOTA = re.compile(r"session limit|usage limit|rate limit reached|insufficient_quota|insufficient balance|余额不足|额度|接口返回 40[12]", re.I)
_REF_LINE = re.compile(r"^\s*(\d+\.?\s*)?(references|bibliography|参考文献)\s*$", re.I | re.M)


def prepare(ws: Workspace) -> None:
    pages = pdfwork.prepare(ws.root)
    source = sourceblocks.ensure(ws.root, pages)
    extra = {}
    try:  # 本地拖进来的 PDF：从第一页的 arXiv 编号或 DOI 补上作者、年份、出处
        first = ws.root / "extract" / "page-001.txt"
        if first.exists():
            extra = sources.enrich(first.read_text(encoding="utf-8", errors="replace"), ws.load("paper").get("meta", {}))
    except Exception:  # noqa: BLE001
        log.exception("补元数据失败 %s", ws.id)

    def apply(paper):
        meta = paper.setdefault("meta", {})
        meta.update({"pages": pages, "page_count": len(pages)})
        for k, v in extra.items():
            if not meta.get(k) or (k == "title_en" and meta.get(k) == meta.get("source", "").removesuffix(".pdf")):
                meta[k] = v
        tr = paper.setdefault("translation", {})
        versions = tr.setdefault("source_versions", {})
        changed = [int(p) for p, entry in source["pages"].items() if p in versions and versions[p] != entry["source_version"]]
        versions.update({p: entry["source_version"] for p, entry in source["pages"].items()})
        if changed and paper.get("reading"):
            for p in changed:
                state = paper["reading"].get("pages", {}).get(str(p))
                if state:
                    state["status"] = "stale"
            paper["reading"]["insights"] = reading.drop_insights_on_pages(paper["reading"].get("insights", []), changed)
            reading.invalidate_synthesis(paper)
            reading.rebuild_guide(paper)
        return changed
    changed = ws.update("paper", apply)
    if changed:
        ws.update("layout", lambda layout: [layout.pop(k) for k in list(layout) if layout[k].get("page") in changed])
        journal(ws, f"原文版本变化，第 {changed} 页旧定位和精读已失效")


def references_page(ws: Workspace) -> int | None:
    """参考文献从哪一页开始（找单独成行的 References 标题）。找不到返回 None。"""
    n = ws.load("paper").get("meta", {}).get("page_count") or 0
    for p in range(2, n + 1):
        f = ws.root / "extract" / f"page-{p:03d}.txt"
        if f.exists() and _REF_LINE.search(f.read_text(encoding="utf-8", errors="replace")):
            return p
    return None


def scope_pages(ws: Workspace, scope: str | None) -> list[int] | None:
    """翻译范围：all 全文；body 到参考文献那页为止；first:N 前 N 页。返回 None 表示全文。"""
    n = ws.load("paper").get("meta", {}).get("page_count") or 0
    if scope == "body":
        ref = references_page(ws)
        return list(range(1, ref + 1)) if ref else None
    if scope and scope.startswith("first:"):
        k = int(scope.split(":", 1)[1] or 0)
        return list(range(1, min(n, k) + 1)) if k > 0 else None
    return None


def _next_head(ws: Workspace, n: int) -> str:
    p = ws.root / "extract" / f"page-{n:03d}.txt"
    return p.read_text(encoding="utf-8")[:1500] if p.exists() else ""


def _normalize(data: dict, pages: list[int], taken: set[str], source: dict | None = None) -> dict:
    """补页码、去掉和已有块撞车的 id、丢掉明显无效的块。"""
    if isinstance(data, list):
        data = {"blocks": data}
    if not isinstance(data, dict) or not isinstance(data.get("blocks", []), list):
        raise engines.EngineError("模型返回的翻译结构不完整：blocks 必须是数组")
    known = {b["id"]: b for b in (source or {}).get("blocks", [])}
    used_sources = set()
    blocks = []
    for b in data.get("blocks") or []:
        if not isinstance(b, dict) or not b.get("type"):
            continue
        b = copy.deepcopy(b)
        b.setdefault("page", pages[0])
        try:
            b["page"] = int(b["page"])
        except (TypeError, ValueError):
            b["page"] = pages[0]
        if b["page"] not in pages:
            continue
        bid = str(b.get("id") or "")
        if source is not None:
            if bid not in known or known[bid]["page"] != b["page"] or bid in taken:
                continue
            ids = b.get("source_ids") or [bid]
            if not isinstance(ids, list) or not ids or ids[0] != bid or any(i not in known or known[i]["page"] != b["page"] or i in used_sources for i in ids):
                continue
            b["source_ids"] = list(dict.fromkeys(ids))
            b["source_en"] = "\n".join(known[i]["en"] for i in b["source_ids"])
            used_sources.update(b["source_ids"])
        else:
            bid = re.sub(r"[^A-Za-z0-9_\-]", "-", bid or f"p{b['page']}-{len(blocks) + 1}")
        base, k = bid, 2
        while bid in taken:
            bid = f"{base}-{k}"
            k += 1
        taken.add(bid)
        b["id"] = bid
        if b["type"] == "figure":
            b.setdefault("src", "")
        blocks.append(b)
    data["blocks"] = blocks
    return data


class BatchIncomplete(engines.EngineError):
    def __init__(self, pages, message):
        super().__init__(message)
        self.pages = list(pages)


def _claim(ws, pages, source, task_id=None):
    """Claim only these pages. A late response cannot replace a newer claim."""
    token = task_id or uuid.uuid4().hex
    versions = {str(p): source["pages"][str(p)]["source_version"] for p in pages}
    tickets = {str(p): f"{token}:{p}" for p in pages}
    def apply(paper):
        tr = paper.setdefault("translation", {})
        tr.setdefault("page_revisions", {}).update(tickets)
        tr.setdefault("source_versions", {}).update(versions)
    ws.update("paper", apply)
    return tickets, versions


def _guard(paper, tickets, versions):
    tr = paper.get("translation") or {}
    return all((tr.get("page_revisions") or {}).get(p) == ticket
               and (tr.get("source_versions") or {}).get(p) == versions.get(p)
               for p, ticket in tickets.items())


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise engines.Cancelled()


def _call(ws, cfg, prompt, images, cancel, kind, pages):
    start = time.monotonic()
    status, error = "completed", ""
    try:
        _cancelled(cancel)
        text = engines.run(cfg, prompt, ws.root, images, cancel)
        _cancelled(cancel)
        return text
    except engines.Cancelled:
        status = "cancelled"
        raise
    except Exception as exc:
        status, error = "failed", str(exc)[:300]
        raise
    finally:
        row = {"at": now_iso(), "engine": cfg.get("engine"), "model": engines.last_model() or engines.who(cfg),
               "kind": kind, "pages": pages, "seconds": round(time.monotonic() - start, 3),
               "status": status, "usage": engines.last_usage(), "error": error}
        with _merge_lock:
            with open(ws.root / "metrics.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _translation_pages(data, source, batch):
    """Cover all body sources; a geometrically proven footer numeral is page furniture."""
    covered = {i for b in data["blocks"] for i in b.get("source_ids", [b["id"]])}
    for b in source["blocks"]:
        box = b.get("box")
        if (b["en"].strip() == str(b["page"]) and box is not None
                and .9 <= box[1] < box[3] <= 1 and .4 <= (box[0] + box[2]) / 2 <= .6):
            covered.add(b["id"])
    allowed_skips = {"header", "footer", "page_number", "watermark"}
    for item in data.get("skipped_blocks") or []:
        if isinstance(item, dict) and item.get("reason") in allowed_skips:
            covered.add(item.get("block_id"))
    valid = []
    for p in batch:
        src = [b for b in source["blocks"] if b["page"] == p]
        blocks = [b for b in data["blocks"] if b["page"] == p]
        missing_text = any((b.get("type") in ("para", "heading", "note") and not str(b.get("zh") or "").strip())
                           or (b.get("type") == "list" and (not b.get("items") or any(not it.get("zh") for it in b["items"])))
                           or (b.get("type") == "math" and not b.get("tex")) for b in blocks)
        if all(b["id"] in covered for b in src) and not missing_text and (blocks or not src or data.get("references") or src and all(b["id"] in covered for b in src)):
            valid.append(p)
    return valid


def _reading_check(data, batch):
    problems, bad = [], set()
    for block in data["blocks"]:
        found = reading.validate_highlights([block])
        problems.extend(found)
        # Invalid highlight anchors/optional fields are local, recoverable errors.
        # Source-bearing annotations and insights below remain strict page failures.
    highlight_errors = len(problems)
    annotations = reading.validate_annotations(data["blocks"])
    problems.extend(annotations)
    for problem in annotations:
        owner = next((b for b in data["blocks"] if problem.startswith(b["id"] + "：")), None)
        bad.update([owner["page"]] if owner else batch)
    insights, issues = reading.validate_insights(data.get("insights"), data["blocks"])
    data["insights"] = insights
    problems.extend(issues)
    if issues:
        for raw in data.get("_raw_insights") or []:
            bid = raw.get("block") if isinstance(raw, dict) else None
            hit = next((b for b in data["blocks"] if b["id"] == bid), None)
            bad.update([hit["page"]] if hit else batch)
    data["_reading_validation"] = {"highlight_errors": highlight_errors,
                                   "annotation_errors": len(annotations), "insight_errors": len(issues)}
    return problems, bad


def _journal_reading_validation(ws, batch, data):
    counts = data["_reading_validation"]
    journal(ws, f"精读校验第 {batch} 页：高亮错误={counts['highlight_errors']}，"
                f"批注错误={counts['annotation_errors']}，研究要点错误={counts['insight_errors']}")


def _taken(ws: Workspace, batch: list[int]) -> set[str]:
    return {b["id"] for b in ws.load("paper").get("blocks", []) if b.get("page") not in batch}


def _problems(data: dict) -> list[str]:
    problems, tex = block_problems(data["blocks"])
    return problems + tex_problems(tex)


def _cap_density(blocks: list[dict]) -> int:
    """防御性上限：单块高亮 ≤ 6、批注 ≤ 3，超出丢弃。密度主要靠提示词控制，这里只兜极端情况。"""
    dropped = 0
    for b in blocks:
        for key, cap in (("highlights", 6), ("annotations", 3)):
            items = b.get(key)
            if isinstance(items, list) and len(items) > cap:
                dropped += len(items) - cap
                if key == "highlights" and any(reading.normalize_importance(it.get("importance")) is not None for it in items):
                    ranked = sorted(enumerate(items), key=lambda row: (
                        -(reading.normalize_importance(row[1].get("importance")) or 0), row[0]))[:cap]
                    text = reading.block_zh(b)
                    def position(row):
                        span = reading.locate_quote(text, row[1].get("quote"), row[1].get("occurrence", 0))
                        return (span[0] if span else len(text), row[0])
                    b[key] = [it for _, it in sorted(ranked, key=position)]
                else:
                    b[key] = items[:cap]
    return dropped


def journal(ws: Workspace, line: str) -> None:
    """每篇论文自己的翻译记录 job.log，页面上“查看记录”看的就是它。"""
    with open(ws.root / "job.log", "a", encoding="utf-8") as f:
        f.write(f"{now_iso()[:19].replace('T', ' ')}  {line}\n")


def _one_batch(ws: Workspace, cfg: dict, batch: list[int], total_pages: int, cancel, say,
               tickets=None, versions=None) -> None:
    started = time.monotonic()
    source = sourceblocks.ensure(ws.root, batch)
    if tickets is None:
        tickets, versions = _claim(ws, batch, source)
    tickets = {str(p): tickets[str(p)] for p in batch}
    versions = {str(p): versions[str(p)] for p in batch}
    mode = engines.image_mode(cfg)
    images = [pdfwork.engine_image(ws.root, n) for n in batch] if mode != "text" else []
    nxt = batch[-1] + 1
    prompt = prompts.translate(ws, batch, mode, _next_head(ws, nxt) if nxt <= total_pages else "",
                               reading=False, source=source)
    text = _call(ws, cfg, prompt, images, cancel, "translate", batch)
    try:
        data = _normalize(engines.parse_json(text), batch, _taken(ws, batch), source)
    except engines.EngineError:
        (ws.root / "extract" / f"failed-{batch[0]:03d}.txt").write_text(text, encoding="utf-8")
        raise
    problems = _problems(data)
    if problems:
        say(f"第 {batch[0]} 页起有 {len(problems)} 处公式或格式问题，正在修正译文")
        try:
            fixed = _normalize(engines.parse_json(_call(ws, cfg, prompts.repair(prompts.dump(data), problems),
                              None, cancel, "translation_repair", batch)), batch, _taken(ws, batch), source)
            if fixed["blocks"] and len(_problems(fixed)) < len(problems):
                data = fixed
        except engines.EngineError as exc:
            journal(ws, f"第 {batch} 页译文修正失败：{exc}")
    good = _translation_pages(data, source, batch)
    invalid = sorted(set(batch) - set(good))
    if not good:
        (ws.root / "extract" / f"failed-{batch[0]:03d}.txt").write_text(text, encoding="utf-8")
        raise BatchIncomplete(batch, "模型译文不完整：未覆盖有效原文块或缺少译文")
    data["blocks"] = [b for b in data["blocks"] if b["page"] in good]
    data.pop("insights", None)
    for block in data["blocks"]:
        block.pop("highlights", None)
        block.pop("annotations", None)
    current_source = sourceblocks.ensure(ws.root, batch)
    def guard(paper):
        _cancelled(cancel)
        if not _guard(paper, tickets, versions) or any(current_source["pages"][p]["source_version"] != v for p, v in versions.items()):
            return False
        paper.setdefault("translation", {}).setdefault("completed_revisions", {}).update({str(p): tickets[str(p)] for p in good})
        return True
    with _merge_lock:
        result = merge_blocks(ws, data, done=good, replace_pages=good, guard=guard,
                              reading={"pages": good, "status": "pending" if cfg.get("reading_enabled", True) else "skipped",
                                       "enabled": bool(cfg.get("reading_enabled", True))})
        if result.get("stale"):
            journal(ws, f"第 {batch} 页版本冲突：旧翻译结果已拒绝")
            return
        _save_checks(ws, data.get("checks"), good)
        for action in (pdfwork.locate, pdfwork.materialize_figures):
            try:
                action(ws.root)
            except Exception:
                log.exception("原文定位/裁图失败 %s", ws.id)
    journal(ws, f"第 {good} 页翻译完成：用时 {round(time.monotonic() - started)} 秒；精读可手动启动")
    if invalid:
        raise BatchIncomplete(invalid, f"第 {invalid} 页译文缺失；合格页面已保存")


def _save_checks(ws: Workspace, checks, batch: list[int]) -> None:
    """模型发现的原文问题 → 页边的“原文核对提示”。重译这几页时，先去掉上次翻译留下的那几条。"""
    pages = {b["id"]: b.get("page") for b in ws.load("paper").get("blocks", [])}
    old = [e["id"] for e in ws.load("discussion").get("entries", [])
           if e.get("kind") == "check" and e.get("by") == "translator" and pages.get(e.get("anchor")) in batch]
    if old:
        ws.update("discussion", lambda d: d.__setitem__("entries", [e for e in d["entries"] if e.get("id") not in old]))
    items = [{"kind": "check", "by": "translator", "anchor": c["anchor"], "quote": str(c.get("quote") or "")[:200],
              "title": str(c.get("title") or "")[:80], "body": str(c["body"])}
             for c in (checks or []) if isinstance(c, dict) and c.get("anchor") in pages and str(c.get("body") or "").strip()]
    if items:
        try:
            add_discussion(ws, items)
        except ValueError as e:
            journal(ws, f"核对提示没存上：{e}")


def translate_pages(ws: Workspace, cfg: dict, pages: list[int], cancel, report) -> dict[int, str]:
    """翻译给定的页（已完成的页会重译并替换）。report(done, total, message)。
    返回译失败的页 {页码: 原因}。"""
    total_pages = ws.load("paper").get("meta", {}).get("page_count") or 0
    pages = sorted(set(p for p in pages if 1 <= int(p) <= total_pages))
    if not pages:
        return {}
    source = sourceblocks.ensure(ws.root, pages)
    tickets, versions = _claim(ws, pages, source, cfg.get("_task_id"))
    def set_scope(paper):
        tr = paper.setdefault("translation", {})
        tr["target_pages"] = list(cfg.get("_target_pages") or range(1, total_pages + 1))
        tr["requested_scope"] = cfg.get("_scope") or ("all" if pages == list(range(1, total_pages + 1)) else "pages")
    ws.update("paper", set_scope)
    size = max(1, int(cfg.get("batch_pages") or 2))
    batches = [pages[i:i + size] for i in range(0, len(pages), size)]
    workers = max(1, min(8, int(cfg.get("concurrency") or 1)))
    state = {"done": 0, "active": set(), "quota": ""}
    failed: dict[int, str] = {}
    lock = threading.Lock()
    journal(ws, f"开始：{len(pages)} 页，{len(batches)} 批，引擎 {engines.ENGINE_NAMES.get(cfg.get('engine'), cfg.get('engine'))}，并发 {workers}")

    def label(batch):
        return f"第 {batch[0]}–{batch[-1]} 页" if len(batch) > 1 else f"第 {batch[0]} 页"

    def say(msg=None):
        with lock:
            active = sorted(state["active"])
            text = msg or ("正在翻译" + "、".join(label(b) for b in active) if active else "正在翻译")
            report(state["done"], len(pages), text)

    def work(batch):
        if cancel.is_set():
            return
        if state["quota"]:  # 额度用完了，不再白跑
            with lock:
                state["done"] += len(batch)
                for n in batch:
                    failed[n] = state["quota"]
            return
        with lock:
            state["active"].add(tuple(batch))
        say()
        err = None
        remaining = list(batch)
        for attempt in range(2):
            try:
                _one_batch(ws, cfg, remaining, total_pages, cancel, say, tickets, versions)
                journal(ws, f"{label(batch)} 完成")
                err = None
                break
            except engines.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                if isinstance(e, BatchIncomplete):
                    remaining = e.pages
                err = str(e) if isinstance(e, engines.EngineError) else f"{type(e).__name__}: {e}"
                journal(ws, f"{label(batch)} 第 {attempt + 1} 次失败：{err[:500]}")
                log.warning("翻译失败 %s %s: %s", ws.id, batch, err[:300])
                if cancel.is_set():
                    raise engines.Cancelled()
                if _QUOTA.search(err):
                    state["quota"] = err[:300]
                    journal(ws, "额度用完，停止翻译剩下的页")
                    break
        with lock:
            state["active"].discard(tuple(batch))
            state["done"] += len(batch)
            if err:
                for n in remaining:
                    failed[n] = err[:300]
        say()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, b) for b in batches]
        for f in futures:
            f.result()  # Cancelled 在这里抛出去
    if cancel.is_set():
        raise engines.Cancelled()
    journal(ws, "结束" + (f"，{len(failed)} 页失败：{sorted(failed)}" if failed else "，全部成功"))
    return failed


def enrich_pages(ws: Workspace, cfg: dict, pages: list[int] | None, cancel, report) -> dict[int, str]:
    """给已翻译的页面单独生成精读标注：不动译文，只生成高亮和批注。
    pages=None 时重做全部已译页。返回失败页 {页码: 原因}。"""
    paper = ws.load("paper")
    done = set(paper.get("translation", {}).get("done_pages", []))
    if pages is None:
        pages = sorted(done)
    else:
        pages = [p for p in pages if p in done]
    pages = sorted(pages)
    if not pages:
        return {}
    source = sourceblocks.ensure(ws.root, pages)
    tickets, versions = _claim(ws, pages, source, cfg.get("_task_id"))
    size = max(1, int(cfg.get("batch_pages") or 2))
    batches = [pages[i:i + size] for i in range(0, len(pages), size)]
    journal(ws, f"精读开始：{len(pages)} 页，{len(batches)} 批")
    failed: dict[int, str] = {}
    lock = threading.Lock()
    processed = 0
    quota = ""
    def work(batch):
        nonlocal processed, quota
        _cancelled(cancel)
        try:
            if quota:
                raise engines.EngineError(quota)
            bad = _one_enrich(ws, cfg, batch, cancel, tickets=tickets, versions=versions)
            if bad:
                with lock:
                    failed.update({p: "精读校验或版本冲突失败，译文保留" for p in bad})
        except engines.Cancelled:
            raise
        except Exception as exc:
            error = str(exc)[:300]
            journal(ws, f"精读第 {batch} 页失败：{error}")
            if _QUOTA.search(error):
                quota = error
            with lock:
                failed.update({p: error for p in batch})
            def mark(paper):
                if _guard(paper, {str(p): tickets[str(p)] for p in batch}, versions):
                    reading.apply_reading_state(paper, batch, "failed", True)
                    reading.rebuild_guide(paper)
            ws.update("paper", mark)
        with lock:
            processed += len(batch)
            report(processed, len(pages), f"精读 {processed}/{len(pages)} 页" + (f"，失败 {len(failed)} 页" if failed else ""))
    with ThreadPoolExecutor(max_workers=max(1, min(8, int(cfg.get("concurrency") or 1)))) as pool:
        for future in [pool.submit(work, batch) for batch in batches]:
            future.result()
    _cancelled(cancel)
    journal(ws, "精读结束" + (f"，{len(failed)} 页失败：{sorted(failed)}" if failed else "，全部成功"))
    if not failed:
        try:
            finalize_reading(ws, cfg, cancel)  # 覆盖变全了就综合研究导航
        except engines.Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 —— 导航失败不影响精读结果
            journal(ws, f"研究导航综合失败：{e}")
            log.warning("研究导航综合失败 %s: %s", ws.id, e)
    return failed


def _one_enrich(ws: Workspace, cfg: dict, batch: list[int], cancel, snapshot=None,
                tickets=None, versions=None, prompt=None, repair=True) -> set[int]:
    """一批已译页的手动精读：用现有译文生成标注，校验后写回块。"""
    snapshot = snapshot or ws.load("paper")
    if tickets is None:
        tickets, versions = _claim(ws, batch, sourceblocks.ensure(ws.root, batch))
    tickets = {str(p): tickets[str(p)] for p in batch}
    hashes = {p: reading.page_content_hash(snapshot.get("blocks", []), p) for p in batch}
    targets = [b for b in snapshot.get("blocks", []) if b.get("page") in batch]
    was_repair = prompt is not None
    prompt = prompt or prompts.enrich(ws, batch, targets)
    text = _call(ws, cfg, prompt, None, cancel, "reading_repair" if was_repair else "enrich", batch)
    try:
        data = engines.parse_json(text)
    except engines.EngineError as exc:
        if not repair:
            raise
        journal(ws, f"第 {batch} 页精读 JSON 解析失败，按固定译文修复一次")
        return _one_enrich(ws, cfg, batch, cancel, snapshot=snapshot, tickets=tickets,
                           versions=versions, prompt=prompts.repair_reading(ws, batch, targets, [str(exc)]),
                           repair=False)
    items = data.get("blocks") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise engines.EngineError("精读结果里没有 blocks")
    known = {b["id"] for b in targets}
    known_items = [it for it in items if isinstance(it, dict) and it.get("id") in known]
    reviewed = set(data.get("reviewed_pages") or []) if isinstance(data, dict) else set()
    if not known_items and not reviewed.issuperset(batch):
        raise engines.EngineError("精读结果未返回有效文字块或已分析页码")
    clean = _clean_enrich_items(ws, items, batch, snapshot, preserve=was_repair)
    raw_clean = copy.deepcopy(clean)
    package = {"blocks": clean, "insights": data.get("insights") if isinstance(data, dict) else [],
               "_raw_insights": data.get("insights") if isinstance(data, dict) else []}
    problems, bad = _reading_check(package, batch)
    _journal_reading_validation(ws, batch, package)
    if len(known_items) != len(items):
        problems.append("精读结果引用未提供的文字块")
        bad.update(batch)
    if problems:
        journal(ws, f"第 {batch} 页精读校验问题：" + "；".join(problems)[:1000])
    original = [b for b in raw_clean if b["page"] in bad]
    _cap_density(clean)
    current_source = sourceblocks.ensure(ws.root, batch)
    def apply(paper):
        _cancelled(cancel)
        if not _guard(paper, tickets, versions) or any(reading.page_content_hash(paper.get("blocks", []), p) != hashes[p] for p in batch):
            return False
        if any(current_source["pages"][str(p)]["source_version"] != versions[str(p)] for p in batch):
            return False
        by_id = {b["id"]: b for b in paper.get("blocks", [])}
        for b in clean:
            target = by_id.get(b["id"])
            if not target:
                continue
            for k in ("highlights", "annotations"):
                if b.get(k):
                    target[k] = b[k]
                else:
                    target.pop(k, None)
        statuses = {p: "failed" if p in bad else "completed" for p in batch}
        paper.setdefault("reading", {})["insights"] = reading.merge_insights(
            reading.drop_insights_on_pages((paper.get("reading") or {}).get("insights", []), batch), package["insights"])
        reading.apply_reading_state(paper, batch, statuses, True)
        reading.rebuild_guide(paper)
        reading_quality.rebuild_quality(paper)
        return True

    with _merge_lock:
        accepted = ws.update("paper", apply)
    if not accepted:
        journal(ws, f"第 {batch} 页精读版本冲突：旧标注已拒绝")
        return set(batch)
    journal(ws, f"第 {batch} 页精读完成" + (f"，{len(problems)} 处标注不合格已丢弃" if problems else ""))
    if bad and repair:
        try:
            return _one_enrich(ws, cfg, sorted(bad), cancel, snapshot=ws.load("paper"), tickets=tickets,
                               versions=versions, prompt=prompts.repair_reading(ws, sorted(bad), original, problems), repair=False)
        except engines.Cancelled:
            raise
        except engines.EngineError as exc:
            journal(ws, f"第 {sorted(bad)} 页精读专项修复失败：{exc}")
    return bad


def finalize_reading(ws: Workspace, cfg: dict, cancel=None) -> bool:
    """手动精读收尾：程序重建研究导航；范围全覆盖且 insights 有更新时做一次模型综合。
    返回是否做了模型综合。"""
    def rebuild(paper):
        reading.rebuild_guide(paper)
        reading_quality.rebuild_quality(paper)
    ws.update("paper", rebuild)
    paper = ws.load("paper")
    guide = paper.get("reading_guide") or {}
    coverage = guide.get("coverage") or {}
    if not (guide.get("status") == "complete" or coverage.get("range_complete")) or not (paper.get("reading") or {}).get("insights"):
        return False
    if guide.get("synth_of") == reading.insights_hash(paper):  # 没有新 insights，不重复综合
        return False
    _synthesize_guide(ws, cfg, cancel)
    return True


def _synthesize_guide(ws: Workspace, cfg: dict, cancel) -> None:
    """全文完成后的研究导航综合：一次模型调用，产出 summary / relations / 核验。"""
    paper = ws.load("paper")
    expected = reading.insights_hash(paper)
    data = engines.parse_json(_call(ws, cfg, prompts.synthesize(ws), None, cancel, "synthesis",
                                    (paper.get("reading_guide", {}).get("coverage") or {}).get("analyzed", [])))
    if not isinstance(data, dict):
        raise engines.EngineError("研究导航综合返回的不是 JSON 对象")
    summary = str(data.get("summary") or "").strip()[:200]
    relations = data.get("relations") if isinstance(data.get("relations"), list) else []
    statuses = data.get("checks") if isinstance(data.get("checks"), list) else []
    def apply(current):
        _cancelled(cancel)
        if reading.insights_hash(current) != expected:
            return False
        reading.apply_synthesis(current, summary, relations, statuses,
                                summary_refs=data.get("summary_refs"), expected_hash=expected)
        return bool(current.get("reading_guide", {}).get("synth_of"))
    with _merge_lock:
        accepted = ws.update("paper", apply)
    journal(ws, "研究导航综合" + (f"完成：{len(relations)} 条候选关系、{len(statuses)} 项候选核验" if accepted else "未通过来源或版本校验，等待重试"))


def _clean_enrich_items(ws: Workspace, items: list, batch: list[int], snapshot=None, preserve=False) -> list[dict]:
    """只保留本批页面上已有的块，带上该块的译文供校验定位；id 不认识的丢弃。"""
    paper = snapshot or ws.load("paper")
    by_id = {b["id"]: b for b in paper.get("blocks", []) if b.get("page") in batch}
    out = {bid: {k: copy.deepcopy(v) for k, v in b.items() if preserve or k not in ("highlights", "annotations")} for bid, b in by_id.items()}
    for it in items:
        if not isinstance(it, dict):
            continue
        src = by_id.get(str(it.get("id") or ""))
        if not src:
            continue
        b = out[src["id"]]
        for key in ("highlights", "annotations"):
            if key in it:
                b[key] = copy.deepcopy(it[key])
    return list(out.values())


def answer(ws: Workspace, cfg: dict, note_id: str, cancel) -> None:
    note = ws.load("reader").get("notes", {}).get(note_id)
    if not note:
        raise KeyError(note_id)
    text = engines.run(cfg, prompts.answer(ws, note), ws.root, None, cancel).strip()
    if not text:
        raise engines.EngineError("模型没有给出回答")
    add_discussion(ws, [{"reply_to": note_id, "kind": "reply", "body": text, "by": engines.who(cfg)}])


def retranslate(ws: Workspace, cfg: dict, key: str, hint: str, cancel) -> None:
    data = engines.parse_json(engines.run(cfg, prompts.retranslate(ws, key, hint), ws.root, None, cancel))
    zh = (data or {}).get("zh", "").strip() if isinstance(data, dict) else ""
    if not zh:
        raise engines.EngineError("模型没有给出新译文")
    set_block_text(ws, key, zh)

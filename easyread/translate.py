"""后台翻译：一批几页交给模型，校验后并进 paper.json。每批落盘，中断了下次接着译。

一批失败会重试；还是失败就记下来跳过，接着译后面的页，最后在页面上给“重试失败的页”。
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor

from . import engines, pdfwork, prompts, sources
from .checks import block_problems, tex_problems
from .log import log
from .paperdata import add_discussion, merge_blocks, set_block_text
from .store import Workspace, now_iso

_merge_lock = threading.Lock()  # 并发翻译时，并入 paper.json 和重算原页定位一次只做一个
# 用量到顶、余额不足这类错误，后面的批次也一定失败：直接停，剩下的页记为没译，等额度恢复后一键重试
_QUOTA = re.compile(r"session limit|usage limit|rate limit reached|insufficient_quota|余额不足|额度|接口返回 40[12]", re.I)
_REF_LINE = re.compile(r"^\s*(\d+\.?\s*)?(references|bibliography|参考文献)\s*$", re.I | re.M)


def prepare(ws: Workspace) -> None:
    pages = pdfwork.prepare(ws.root)
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
    ws.update("paper", apply)


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


def _normalize(data: dict, pages: list[int], taken: set[str]) -> dict:
    """补页码、去掉和已有块撞车的 id、丢掉明显无效的块。"""
    if isinstance(data, list):
        data = {"blocks": data}
    blocks = []
    for b in data.get("blocks") or []:
        if not isinstance(b, dict) or not b.get("type"):
            continue
        b.setdefault("page", pages[0])
        try:
            b["page"] = int(b["page"])
        except (TypeError, ValueError):
            b["page"] = pages[0]
        bid = re.sub(r"[^A-Za-z0-9_\-]", "-", str(b.get("id") or f"p{b['page']}-{len(blocks) + 1}"))
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


def _taken(ws: Workspace, batch: list[int]) -> set[str]:
    return {b["id"] for b in ws.load("paper").get("blocks", []) if b.get("page") not in batch}


def _problems(data: dict) -> list[str]:
    problems, tex = block_problems(data["blocks"])
    return problems + tex_problems(tex)


def journal(ws: Workspace, line: str) -> None:
    """每篇论文自己的翻译记录 job.log，页面上“查看记录”看的就是它。"""
    with open(ws.root / "job.log", "a", encoding="utf-8") as f:
        f.write(f"{now_iso()[:19].replace('T', ' ')}  {line}\n")


def _one_batch(ws: Workspace, cfg: dict, batch: list[int], total_pages: int, cancel, say) -> None:
    mode = engines.image_mode(cfg)
    images = [pdfwork.engine_image(ws.root, n) for n in batch] if mode != "text" else []
    nxt = batch[-1] + 1
    prompt = prompts.translate(ws, batch, mode, _next_head(ws, nxt) if nxt <= total_pages else "")
    text = engines.run(cfg, prompt, ws.root, images, cancel)
    try:
        data = engines.parse_json(text)
    except engines.EngineError:
        (ws.root / "extract" / f"failed-{batch[0]:03d}.txt").write_text(text, encoding="utf-8")
        raise
    data = _normalize(data, batch, _taken(ws, batch))
    problems = _problems(data)
    if problems:  # 给一次修的机会
        say(f"第 {batch[0]} 页起有 {len(problems)} 处公式或格式问题，正在让模型修正")
        try:
            fixed = engines.parse_json(engines.run(cfg, prompts.repair(prompts.dump(data), problems), ws.root, None, cancel))
            fixed = _normalize(fixed, batch, _taken(ws, batch))
            if fixed["blocks"] and len(_problems(fixed)) < len(problems):
                data = fixed
        except engines.EngineError as e:
            journal(ws, f"第 {batch} 页修正失败，保留原译：{e}")
    if not data["blocks"] and not data.get("references"):  # 整页都是参考文献时只有 references，没有新块，也算译完
        raise engines.EngineError("模型没有译出任何内容")
    with _merge_lock:
        data = _normalize(data, batch, _taken(ws, batch))  # 并发时别的批可能刚占用了同名 id
        merge_blocks(ws, data, done=batch, replace_pages=batch)
        _save_checks(ws, data.get("checks"), batch)
        try:
            pdfwork.locate(ws.root)
        except Exception:  # noqa: BLE001 —— 定位失败不影响阅读
            log.exception("locate 失败 %s", ws.id)
        try:
            pdfwork.materialize_figures(ws.root)
        except Exception:  # noqa: BLE001 —— 裁图失败不影响阅读
            log.exception("裁图失败 %s", ws.id)


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
        for attempt in range(2):
            try:
                _one_batch(ws, cfg, batch, total_pages, cancel, say)
                journal(ws, f"{label(batch)} 完成")
                err = None
                break
            except engines.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
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
                for n in batch:
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

"""后台任务：导入后的渲染与整篇翻译（一条队列，状态落在每篇的 job.json，重启后接着做），
以及“让模型回答我的问题 / 重译这段”这类小任务（另一条队列，不用等整篇翻译）。"""
from __future__ import annotations

import queue
import threading
import time

from . import config, deepread, obsidian, translate
from .engines import Cancelled, EngineError
from .library import Library
from .log import log
from .store import Workspace, now_iso


class Jobs:
    def __init__(self, lib: Library):
        self.lib = lib
        self.bulk: queue.Queue[str] = queue.Queue()
        self.small: queue.Queue[dict] = queue.Queue()
        self.cancels: dict[str, threading.Event] = {}
        self.recent: list[dict] = []  # 小任务的状态，给页面轮询
        self.lock = threading.Lock()
        for target in (self._bulk_loop, self._small_loop):
            threading.Thread(target=target, daemon=True).start()
        self._resume()

    # ---------- 整篇 ----------
    def _write(self, ws: Workspace, **fields):
        def apply(job):
            job.update(fields)
            job["updated"] = now_iso()
        ws.update("job", apply)

    def enqueue(self, ws: Workspace, pages: list[int] | None = None, translate_after: bool = True, scope: str | None = None):
        """pages=None：按 scope（all / body / first:N）翻译还没译的页。"""
        self._write(ws, type="translate" if translate_after else "prepare", state="queued", message="排队中",
                    pages=pages, scope=scope or "all", translate=translate_after, done=0, total=0, error="", failed={})
        self.bulk.put(ws.id)

    def cancel(self, pid: str):
        ev = self.cancels.get(pid)
        if ev:
            ev.set()
        ws = self.lib.ws(pid)
        if ws and (ws.load("job") or {}).get("state") == "queued":
            self._write(ws, state="cancelled", message="已取消")

    def _resume(self):
        for ws in self.lib.all():
            job = ws.load("job") or {}
            if job.get("state") in ("queued", "running"):
                self._write(ws, state="queued", message="排队中（服务重启后继续）")
                self.bulk.put(ws.id)
            state = deepread.read(ws)
            if state["state"] in ("queued", "running"):
                deepread.set_state(ws, state="queued", message="排队中（服务重启后继续）")
                self.small.put({"id": f"j{int(time.time() * 1000)}", "kind": "deepread", "pid": ws.id})

    def _bulk_loop(self):
        while True:
            pid = self.bulk.get()
            ws = self.lib.ws(pid)
            if not ws:
                continue
            job = ws.load("job") or {}
            if job.get("state") != "queued":
                continue
            cancel = self.cancels[pid] = threading.Event()
            try:
                self._run_bulk(ws, job, cancel)
            except Cancelled:
                self._write(ws, state="cancelled", message="已取消，已译的部分保留")
            except Exception as e:  # noqa: BLE001
                msg = str(e) if isinstance(e, (EngineError, KeyError, ValueError)) else f"{type(e).__name__}: {e}"
                self._write(ws, state="error", message="出错了", error=msg[:800])
                log.exception("后台任务出错 %s", pid)
                try:
                    translate.journal(ws, f"出错停止：{msg[:500]}")
                except OSError:
                    pass
            finally:
                self.cancels.pop(pid, None)

    def _run_bulk(self, ws: Workspace, job: dict, cancel: threading.Event):
        cfg = config.load()
        if not ws.load("paper").get("meta", {}).get("pages"):
            self._write(ws, state="running", message="正在渲染原页、抽取文字")
            translate.prepare(ws)
        if not job.get("translate") or cfg.get("engine") == "none":
            self._write(ws, state="done", message="已导入" + ("（未开启自动翻译）" if job.get("translate") else ""))
            return
        paper = ws.load("paper")
        all_pages = [p["n"] for p in paper["meta"]["pages"]]
        done_pages = set(paper.get("translation", {}).get("done_pages", []))
        wanted = job.get("pages") or translate.scope_pages(ws, job.get("scope")) or all_pages
        pages = wanted if job.get("pages") else [n for n in wanted if n not in done_pages]
        if not pages:
            self._write(ws, state="done", message="选定范围已译完")
            return

        def report(done, total, message):
            if cancel.is_set():
                raise Cancelled()
            self._write(ws, state="running", done=done, total=total, message=message)

        started = time.time()
        failed = translate.translate_pages(ws, cfg, pages, cancel, report)
        minutes = max(1, round((time.time() - started) / 60))
        if failed:
            first = next(iter(failed.values()))
            self._write(ws, state="partial", failed={str(k): v for k, v in failed.items()}, error=first,
                        message=(f"{len(pages) - len(failed)} 页译好了，" if len(failed) < len(pages) else "") + f"{len(failed)} 页没译成功")
        else:
            self._write(ws, state="done", failed={}, error="", message=f"翻译完成（{len(pages)} 页，用时约 {minutes} 分钟）")

    # ---------- 小任务 ----------
    def submit_small(self, kind: str, pid: str, **kw) -> dict:
        job = {"id": f"j{int(time.time() * 1000)}", "kind": kind, "pid": pid, "state": "queued", "message": "排队中",
               "at": now_iso(), **kw}
        with self.lock:
            self.recent = ([job] + self.recent)[:50]
        self.small.put(job)
        return job

    def submit_deepread(self, ws: Workspace, cfg: dict | None = None) -> dict:
        """只由用户明确调用时排队；同一篇论文的活动任务不重复排队。"""
        source = cfg or config.load()
        setting = source.get("deepread") or {}
        chat = source.get("chat") or {}
        state, created = deepread.claim(ws, setting.get("model") or chat.get("default") or "",
                                        setting.get("prompt") or deepread.DEFAULT_PROMPT)
        if created:
            self.submit_small("deepread", ws.id)
        return state

    def small_status(self, pid: str | None = None) -> list[dict]:
        with self.lock:
            return [dict(j) for j in self.recent if not pid or j["pid"] == pid]

    def _small_loop(self):
        while True:
            job = self.small.get()
            ws = self.lib.ws(job["pid"])
            if not ws:
                continue
            job["state"], job["message"] = "running", "模型思考中"
            try:
                cfg = config.load()
                if job["kind"] == "answer":
                    translate.answer(ws, cfg, job["note"], None)
                elif job["kind"] == "retranslate":
                    translate.retranslate(ws, cfg, job["key"], job.get("hint", ""), None)
                elif job["kind"] == "deepread":
                    deepread.set_state(ws, state="running", message="模型思考中", error="")
                    deepread.generate(ws, cfg, None)
                    obsidian.auto_sync(ws, cfg)
                job["state"], job["message"] = "done", "完成"
            except Exception as e:  # noqa: BLE001
                job["state"], job["message"] = "error", str(e)[:500]
                if job["kind"] == "deepread":
                    deepread.set_state(ws, state="error", message="精读失败", error=str(e)[:500])
                log.exception("小任务出错 %s", job.get("kind"))

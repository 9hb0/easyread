"""把精读文档送进知识库自带的 paper-ingest 入库流程。

知识库（比如 D:\Agent\wiki\3D&4D）的入库入口是
automation/paper-ingest/workflow.ps1 -Workflow ingestion：接收外部精读文档（.md）和原论文主 PDF，
由知识库独立配置的入库模型整理候选、做完整性筛查，通过后发布笔记并更新受管索引。

这个模块只负责：校验输入、拼命令、后台起进程、把状态落到每篇论文的 wiki_ingest.json。
不改动知识库内部的规则；-AllowModelTransfer 授权的只有本次点击提交的精读文档和 PDF。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from .log import log
from .store import Workspace, now_iso, read_json, write_json_atomic

_STATE_DEFAULT = {
    "state": "idle",  # idle | running | done | error | cancelled
    "message": "还没有入库",
    "updated": "",
    "vault": "",
    "run_id": "",
    "run_dir": "",
    "pid": None,
    "result": None,
}

_PROCS: dict[str, subprocess.Popen] = {}
_PROCS_LOCK = threading.Lock()


def settings(cfg: dict) -> dict:
    w = cfg.get("wiki") or {}
    return {"vault": str(w.get("vault") or "").strip()}


def _state_path(ws: Workspace) -> Path:
    return ws.root / "wiki_ingest.json"


def _log_path(ws: Workspace) -> Path:
    return ws.root / "wiki-ingest.log"


def _load_state(ws: Workspace) -> dict:
    state = dict(_STATE_DEFAULT)
    state.update(read_json(_state_path(ws), {}) or {})
    return state


def _save_state(ws: Workspace, state: dict) -> None:
    state["updated"] = now_iso()
    write_json_atomic(_state_path(ws), state)


def _set_state(ws: Workspace, **fields) -> dict:
    state = _load_state(ws)
    state.update(fields)
    _save_state(ws, state)
    return state


def _log_tail(ws: Workspace, limit: int = 20000) -> str:
    try:
        text = _log_path(ws).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if len(text) <= limit else "…\n" + text[-limit:]


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _build_command(script: Path, vault: Path, document: Path, main_pdf: Path, run_id: str) -> list[str]:
    shell = "powershell" if sys.platform == "win32" else "pwsh"
    return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-Workflow", "ingestion", "-VaultRoot", str(vault),
            "-InputFile", str(document), "-MainPdf", str(main_pdf),
            "-RunId", run_id, "-AllowModelTransfer"]


def _parse_result(text: str) -> dict | None:
    """workflow_runtime 最后会把结果 JSON 打到标准输出；从后往前找完整的 JSON 对象。"""
    lines = (text or "").splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() != "{":
            continue
        try:
            data = json.loads("\n".join(lines[i:]))
        except ValueError:
            continue
        if isinstance(data, dict) and "status" in data:
            return data
    return None


def _load_run_result(run_dir) -> dict | None:
    if not run_dir:
        return None
    try:
        data = json.loads((Path(run_dir) / "workflow-result.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "status" in data else None


def _result_message(result: dict) -> str:
    status = str(result.get("status") or "")
    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    errors = "；".join(str(e) for e in (result.get("errors") or []) if str(e).strip())
    warnings = "；".join(str(w) for w in (result.get("warnings") or []) if str(w).strip())
    if status == "published":
        outcome = str(inner.get("outcome") or "")
        note = str(inner.get("note_path") or "")
        labels = {"verified": "已发布到知识库",
                  "needs_review": "已放进待归档（needs_review），未动正式索引",
                  "duplicate": "知识库里已有这篇，未重复发布"}
        msg = labels.get(outcome, "入库流程结束（%s）" % (outcome or status))
        if note:
            msg += "：" + note
        extra = errors or warnings
        if extra:
            msg += "。" + extra[:300]
        return msg
    labels = {
        "failed": "入库失败",
        "blocked": "入库检查未通过，候选保留在知识库 tmp",
        "awaiting_source": "缺原论文 PDF，候选保留在知识库 tmp",
        "runtime_failed": "入库模型执行失败，详见日志",
        "ingestion_setup_failed": "入库阶段没能启动",
        "prepared": "只准备了运行目录，没有执行模型",
    }
    msg = labels.get(status, "入库结束：%s" % (status or "未知状态"))
    if errors:
        msg += "：" + errors[:300]
    return msg


def _execute(ws: Workspace, cmd: list[str], log_path: Path) -> tuple[int, str]:
    """跑 workflow.ps1：输出边收边写日志，登记进程句柄供取消用。"""
    parts: list[str] = []
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")  # 结果 JSON 是 ensure_ascii=False，管道下强制子进程用 UTF-8
    with open(log_path, "w", encoding="utf-8") as logf:
        logf.write("命令：%s\n\n" % json.dumps(cmd, ensure_ascii=False))
        logf.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env)
        with _PROCS_LOCK:
            _PROCS[ws.id] = proc
        _set_state(ws, pid=proc.pid)
        for line in proc.stdout:
            parts.append(line)
            logf.write(line)
        logf.flush()
        rc = proc.wait()
    with _PROCS_LOCK:
        _PROCS.pop(ws.id, None)
    return rc, "".join(parts)


def _run(ws: Workspace, cmd: list[str], run_id: str, run_dir: str) -> None:
    try:
        rc, output = _execute(ws, cmd, _log_path(ws))
    except OSError as e:
        _set_state(ws, state="error", message="启动入库流程失败：%s" % e)
        return
    if _load_state(ws).get("state") == "cancelled":
        return
    result = _parse_result(output) or _load_run_result(run_dir)
    if result is None:
        _set_state(ws, state="error", run_id=run_id, run_dir=run_dir,
                   message="入库流程异常退出（退出码 %s），详见日志" % rc)
        return
    _set_state(ws, state="done" if result.get("status") == "published" else "error",
               message=_result_message(result), result=result, run_id=run_id, run_dir=run_dir)


def read(ws: Workspace) -> dict:
    state = _load_state(ws)
    if state.get("state") == "running":
        result = _load_run_result(state.get("run_dir"))
        if result is not None:  # 服务重启后进程自己跑完了：把结果收回来
            state.update(state="done" if result.get("status") == "published" else "error",
                         message=_result_message(result), result=result)
            _save_state(ws, state)
        elif state.get("pid") and not _pid_alive(state["pid"]):
            state.update(state="error", message="入库中断（进程退出且没有结果），可重新入库")
            _save_state(ws, state)
    state["log"] = _log_tail(ws)
    return state


def start(ws: Workspace, cfg: dict) -> dict:
    vault = settings(cfg)["vault"]
    if not vault:
        raise ValueError("还没有设置知识库路径：设置 → 精读论文 → 论文入库")
    root = Path(vault)
    if not root.is_dir():
        raise ValueError("知识库目录不存在：%s" % vault)
    script = root / "automation" / "paper-ingest" / "workflow.ps1"
    if not script.is_file():
        raise ValueError("这个目录里没有入库流程（找不到 automation\\paper-ingest\\workflow.ps1）")
    document = ws.root / "deepread.md"
    try:
        content = document.read_text(encoding="utf-8")
    except OSError:
        content = ""
    if not content.strip():
        raise ValueError("还没有精读文档，先生成精读")
    main_pdf = ws.root / "source.pdf"
    if not main_pdf.is_file():
        raise ValueError("找不到原论文 PDF（source.pdf）")
    current = read(ws)
    if current["state"] == "running" and _pid_alive(current.get("pid")):
        raise ValueError("这篇已经在入库中")
    run_id = "easyread-%s-%s" % (ws.id, datetime.now().strftime("%Y%m%dT%H%M%S"))
    run_dir = root / "tmp" / "paper-ingest" / run_id
    _set_state(ws, state="running", message="知识库入库中", vault=str(root),
               run_id=run_id, run_dir=str(run_dir), pid=None, result=None)
    cmd = _build_command(script, root, document, main_pdf, run_id)
    threading.Thread(target=_run, args=(ws, cmd, run_id, str(run_dir)), daemon=True).start()
    log.info("知识库入库已启动 %s -> %s", ws.id, run_id)
    return read(ws)


def cancel(ws: Workspace) -> dict:
    state = _load_state(ws)
    if state.get("state") != "running":
        return {"ok": False, "message": "没有正在运行的入库"}
    with _PROCS_LOCK:
        proc = _PROCS.get(ws.id)
    pid = (proc.pid if proc is not None else None) or state.get("pid")
    if not pid:
        return {"ok": False, "message": "还没拿到入库进程，稍等再试"}
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.kill(int(pid), 15)
    except (OSError, ValueError) as e:
        return {"ok": False, "message": "取消失败：%s" % e}
    _set_state(ws, state="cancelled", message="已取消入库；已生成的候选保留在知识库 tmp 里")
    return {"ok": True}

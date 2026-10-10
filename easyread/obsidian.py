"""把精读文档同步到 Obsidian 知识库。

每篇论文的 deepread.md 转成 Obsidian 友好的格式（$ 公式、YAML 属性、按标题命名），
写进设置的 vault 路径。library 目录仍是唯一数据源，Obsidian 里是同步出来的副本：
内容变了自动更新，标题改了会清掉旧文件。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config
from .library import Library, _link
from .log import log
from .store import Workspace, now_iso, read_json, write_json_atomic

DEFAULT_FOLDER = "EasyRead 精读"


def settings(cfg: dict) -> dict:
    o = dict(cfg.get("obsidian") or {})
    vault = str(o.get("vault") or "").strip()
    folder = str(o.get("folder") or "").strip() or DEFAULT_FOLDER
    if folder.startswith(("/", "\\")) or Path(folder).is_absolute() or ".." in Path(folder).parts or folder == ".":
        folder = DEFAULT_FOLDER
    return {"vault": vault, "folder": folder, "auto": bool(o.get("auto", True)), "classify": bool(o.get("classify"))}


# ---------- 公式：\(x\) → $x$，\[x\] → $$x$$（代码块里不动） ----------
_CODE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")


def _convert_math(text: str) -> str:
    text = re.sub(r"\\\((.+?)\\\)", r"$\1$", text)          # 行内公式
    text = re.sub(r"\\\[([\s\S]+?)\\\]", r"$$\1$$", text)  # 独立公式
    return text


def math_to_obsidian(text: str) -> str:
    parts = _CODE.split(text or "")
    return "".join(p if i % 2 else _convert_math(p) for i, p in enumerate(parts))


# ---------- 从 paper.json / item.json 取元数据（和文献库列表同一套逻辑） ----------
def _meta(ws: Workspace) -> dict:
    paper = ws.load("paper") or {}
    meta = dict(paper.get("meta") or {})
    item = ws.load("item") or {}
    meta.update({k: v for k, v in (item.get("meta_override") or {}).items() if v})
    return meta


def _y(value) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def frontmatter(ws: Workspace, cfg: dict) -> str:
    meta = _meta(ws)
    item = ws.load("item") or {}
    title = (meta.get("title_zh") or meta.get("title_en") or "").strip()
    lines = ["---", "title: " + _y(title or ws.id),
             "easyread_id: " + ws.id,  # 放前面，改名清理时只读文件头就能认出来
             "easyread: " + _y(f"http://127.0.0.1:{cfg.get('port') or 8765}/read/{ws.id}")]
    if meta.get("title_en") and meta["title_en"] != title:
        lines.append("aliases: [" + _y(meta["title_en"]) + "]")
    state = read_json(ws.root / "deepread.json", {}) or {}
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(state.get("updated") or ""))
    if m:
        lines.append("created: " + m.group(0))  # 精读生成日期，和库里 created 属性的习惯一致
    authors = [a.strip() for a in re.split(r"[,;，；]", meta.get("authors") or "") if a.strip()]
    if authors:
        lines.append("authors: [" + ", ".join(_y(a) for a in authors[:30]) + "]")
    if meta.get("year"):
        lines.append("year: " + _y(str(meta["year"])))
    for key in ("venue", "arxiv", "doi"):
        if meta.get(key):
            lines.append(key + ": " + _y(meta[key]))
    link = _link(meta)
    if link:
        lines.append("url: " + _y(link))
    tags = list(dict.fromkeys([t for t in (item.get("tags") or []) if t] + ["easyread", "精读"]))
    if tags:
        lines.append("tags: [" + ", ".join(_y(t) for t in tags) + "]")
    lines.append("---")
    return "\n".join(lines)


_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def note_filename(ws: Workspace) -> str:
    meta = _meta(ws)
    title = (meta.get("title_zh") or meta.get("title_en") or ws.id).strip()
    name = re.sub(r"\s+", " ", _UNSAFE.sub(" ", title)).strip(" .")
    return (name[:80] or ws.id) + ".md"


def build_note(ws: Workspace, cfg: dict) -> str | None:
    try:
        content = (ws.root / "deepread.md").read_text(encoding="utf-8")
    except OSError:
        return None
    if not content.strip():
        return None
    return frontmatter(ws, cfg) + "\n\n" + math_to_obsidian(content).strip() + "\n"


def _owned_by(path: Path, pid: str) -> bool:
    """文件头部的 frontmatter 里带 easyread_id: pid，说明是这篇论文同步出来的。"""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]  # 旧版布局的 id 在长作者列表后面，头部 2000 字够用
    except OSError:
        return False
    return f"easyread_id: {pid}" in head


# ---------- 自动归类：按库里带编号的主题文件夹（00_、01_…）选一个 ----------
def classify_folders(vault: Path) -> list[str]:
    try:
        return [p.name for p in sorted(vault.iterdir()) if p.is_dir() and p.name[:1].isdigit()]
    except OSError:
        return []


def cached_folder(ws: Workspace, targets: list[str]) -> str | None:
    if not targets:
        return None
    f = str((read_json(ws.root / "obsidian.json", {}) or {}).get("folder") or "")
    return f if f in targets else None


def store_folder(ws: Workspace, folder: str) -> None:
    write_json_atomic(ws.root / "obsidian.json", {"folder": folder, "at": now_iso()})


def classify_paper(ws: Workspace, cfg: dict, targets: list[str]) -> str | None:
    """让模型从主题文件夹里选一个；失败或答不上来返回 None（落到默认文件夹）。"""
    from . import chat_models, engines
    meta = _meta(ws)
    prompt = ("下面是一个研究知识库的文件夹列表，每行一个：\n" + "\n".join(targets) +
              "\n\n请根据论文的标题、摘要和出处，从列表里选出最合适的一个文件夹。"
              "只输出那个文件夹名，和列表里的写法完全一致，不要解释、不要加标点。列表里没有合适的就只输出：其他\n\n"
              "论文标题：" + (meta.get("title_zh") or meta.get("title_en") or "") +
              "\n英文标题：" + (meta.get("title_en") or "") +
              "\n出处：" + (meta.get("venue") or meta.get("arxiv") or "") +
              "\n摘要：" + str(meta.get("abstract_en") or "")[:800])
    attempts = []  # 先用精读/问 AI 的模型；不行就用翻译引擎兜底
    requested = (cfg.get("deepread") or {}).get("model") or (cfg.get("chat") or {}).get("default")
    if requested:
        try:
            attempts.append(chat_models.engine_cfg(cfg, requested)[0])
        except Exception:  # noqa: BLE001
            log.exception("解析归类模型失败，改用翻译引擎 %s", ws.id)
    attempts.append(cfg)
    for engine_cfg in attempts:
        try:
            answer = str(engines.run(engine_cfg, prompt, ws.root, [], None) or "").strip()
        except Exception:  # noqa: BLE001
            log.exception("精读归类失败 %s", ws.id)
            continue
        for t in targets:
            if t in answer:
                store_folder(ws, t)
                return t
    return None


def sync_paper(ws: Workspace, cfg: dict, vault: str | None = None, folder: str | None = None, classify: bool = False) -> dict:
    o = settings(cfg)
    vault = (vault or o["vault"]).strip()
    folder_override = bool(folder)  # 显式指定文件夹时按指定的来，不做自动归类
    folder = folder or o["folder"]
    if not vault:
        return {"id": ws.id, "status": "skipped", "message": "还没设置 Obsidian 库路径"}
    root = Path(vault)
    if not root.is_dir():
        return {"id": ws.id, "status": "error", "message": f"目录不存在：{vault}"}
    note = build_note(ws, cfg)
    if note is None:
        return {"id": ws.id, "status": "skipped", "message": "还没有精读文档"}
    topic_dirs = classify_folders(root)
    targets = topic_dirs if (o["classify"] and not folder_override) else []
    sub = cached_folder(ws, targets)
    if sub is None and targets and classify:
        sub = classify_paper(ws, cfg, targets)
    target_dir = root / sub if sub else root / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / note_filename(ws)
    if target.exists() and not _owned_by(target, ws.id):
        stem = target.stem  # 同名文件是别人的（别的论文或自己的笔记）：不覆盖，标题后面加上 id 区分
        target = target_dir / f"{stem} {ws.id}.md"
    try:
        for d in {target_dir, root / folder, *(root / t for t in topic_dirs)}:  # 标题改了/换了文件夹：旧文件清掉
            if not d.is_dir():
                continue
            for old in d.glob("*.md"):
                if old != target and _owned_by(old, ws.id):
                    old.unlink()
    except OSError:
        pass
    try:
        if target.exists() and target.read_text(encoding="utf-8") == note:
            return {"id": ws.id, "status": "unchanged", "file": str(target)}
    except OSError:
        pass
    target.write_text(note, encoding="utf-8", newline="\n")
    return {"id": ws.id, "status": "written", "file": str(target)}


def sync_all(cfg: dict, pid: str | None = None, vault: str | None = None, folder: str | None = None, classify: bool = False) -> dict:
    lib = Library(config.library_dir(cfg))
    papers = lib.all()
    if pid:
        papers = [ws for ws in papers if ws.id == pid or ws.id.startswith(pid)]
    else:
        papers = [ws for ws in papers if (ws.root / "deepread.md").exists()]
    results = []
    for ws in papers:
        try:
            results.append(sync_paper(ws, cfg, vault, folder, classify))
        except Exception as e:  # noqa: BLE001
            results.append({"id": ws.id, "status": "error", "message": f"{type(e).__name__}: {e}"})
    counts = {k: sum(1 for r in results if r["status"] == k) for k in ("written", "unchanged", "skipped", "error")}
    return {"results": results, "total": len(results), **counts}


def auto_sync(ws: Workspace, cfg: dict) -> None:
    """精读生成后自动同步；没配置或出错都不影响精读本身。"""
    o = settings(cfg)
    if not (o["vault"] and o["auto"]):
        return
    try:
        r = sync_paper(ws, cfg, classify=o["classify"])
        if r["status"] == "written":
            log.info("精读已同步到 Obsidian：%s", r.get("file"))
    except Exception:  # noqa: BLE001
        log.exception("同步 Obsidian 失败 %s", ws.id)


# ---------- 找本机的 Obsidian 库（设置页自动填路径用） ----------
_SKIP_DIRS = {"appdata", "node_modules", ".git", ".venv", "site-packages", "__pycache__", ".cache", ".trash", ".obsidian"}


def find_vaults(roots: list[Path] | None = None, max_depth: int = 3, limit: int = 12) -> list[str]:
    home = Path.home()
    candidates = roots or [home, home / "Documents", home / "Desktop", home / "Downloads",
                           home / "OneDrive" / "Documents", home / "Obsidian", home / "Documents" / "Obsidian"]
    seen: set[str] = set()
    found: list[str] = []
    queue: list[tuple[Path, int]] = []
    for p in candidates:
        if p.is_dir() and str(p) not in seen:
            seen.add(str(p))
            queue.append((p, 0))
    while queue and len(found) < limit:
        path, depth = queue.pop(0)
        if (path / ".obsidian").is_dir():
            found.append(str(path))
            continue  # 库里面不用再往下找
        if depth >= max_depth:
            continue
        try:
            children = sorted(path.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.name.lower() in _SKIP_DIRS or child.name.startswith("$"):
                continue
            key = str(child)
            if key not in seen:
                seen.add(key)
                queue.append((child, depth + 1))
    return found

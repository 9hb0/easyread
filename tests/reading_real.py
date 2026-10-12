"""Opt-in real-paper acceptance runner; never run by unittest discovery.

python tests/reading_real.py prepare
python tests/reading_real.py enrich
python tests/reading_real.py ab
Outputs stay in ignored artifacts/reading-qa, with no credentials or user edits.
"""
import argparse
import copy
import json
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from easyread import config, reading, translate
from easyread.store import Workspace, write_json_atomic

OUT = ROOT / "artifacts" / "reading-qa"
SAMPLES = {
    "03112e8bdaeb": [1, 2, 3, 4, 5, 6, 7, 12],
    "f24268730b6e": [1, 2, 4, 5, 6, 7, 9, 12, 17, 19],
    "ca859378a829": [1, 3, 5, 7, 9, 10, 14, 18, 22, 24, 35, 47],
}


def prepare():
    for pid, pages in SAMPLES.items():
        src, dst = ROOT / "library" / pid, OUT / "library" / pid
        if (dst / "paper.json").exists():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        paper = json.loads((src / "paper.json").read_text(encoding="utf-8"))
        assert set(pages) <= set(paper["translation"]["done_pages"])
        assert all(any(b.get("page") == p for b in paper["blocks"]) for p in pages)
        paper["blocks"] = [b for b in paper["blocks"] if b.get("page") in pages]
        for b in paper["blocks"]:
            b.pop("highlights", None)
            b.pop("annotations", None)
        paper.pop("reading", None)
        paper.pop("reading_guide", None)
        paper["translation"].update(done_pages=pages, target_pages=pages,
                                    requested_scope="all" if len(pages) == paper["meta"]["page_count"] else "pages")
        write_json_atomic(dst / "paper.json", paper)
        (dst / "extract").mkdir(exist_ok=True)
        for directory in ("extract", "pages", "figures"):
            if (src / directory).exists():
                shutil.copytree(src / directory, dst / directory, dirs_exist_ok=True)
        shutil.copy2(src / "source.pdf", dst / "source.pdf")
        write_json_atomic(dst / "item.json", {"added": "2026-10-12", "tags": ["精读验收"], "status": "unread"})
        write_json_atomic(dst / "reader.json", {"schema": 2, "rev": 0, "edits": {}, "notes": {}, "progress": {}})
    write_json_atomic(OUT / "manifest.json", {"date": "2026-10-12", "model": config.load()["openai"]["model"], "samples": SAMPLES})
    print("Prepared isolated copies:", sum(map(len, SAMPLES.values())), "pages", flush=True)


def enrich():
    cfg = dict(config.load(), reading_enabled=True, concurrency=3, batch_pages=2)
    result = {}
    for pid, selected in SAMPLES.items():
        ws = Workspace(OUT / "library" / pid)
        pages = reading.pages_needing_reading(ws.load("paper"))
        started = time.monotonic()
        result[pid] = {"failed": translate.enrich_pages(ws, cfg, pages, threading.Event(),
                             lambda d, t, m: print(pid, m, flush=True)),
                       "seconds": round(time.monotonic() - started, 2)}
        write_json_atomic(OUT / "enrich-results.json", result)
        review(ws)


def review(ws):
    paper = ws.load("paper")
    lines = [paper["meta"].get("title_en", "")]
    for b in paper["blocks"]:
        lines += [f"\nPAGE {b['page']} BLOCK {b['id']} {b.get('type')}",
                  "EN: " + (b.get("source_en") or b.get("en") or b.get("caption_en") or ""),
                  "ZH: " + reading.block_zh(b)]
        if b.get("type") in ("math", "table", "list"):
            lines.append(json.dumps({k: b.get(k) for k in ("tex", "head", "rows", "items")}, ensure_ascii=False))
        lines += ["H: " + json.dumps(b.get("highlights", []), ensure_ascii=False),
                  "A: " + json.dumps(b.get("annotations", []), ensure_ascii=False)]
    lines.append("INSIGHTS: " + json.dumps((paper.get("reading") or {}).get("insights"), ensure_ascii=False))
    (OUT / (ws.id + "-review.txt")).write_text("\n".join(lines), encoding="utf-8")


def ab():
    src = OUT / "library" / "03112e8bdaeb"
    results = {}
    # The identical source pages, prompt context and model are used in both arms.
    for enabled in (False, True):
        dst = OUT / ("ab-reading" if enabled else "ab-plain")
        dst.mkdir(exist_ok=True)
        shutil.copytree(src / "extract", dst / "extract", dirs_exist_ok=True)
        shutil.copytree(src / "pages", dst / "pages", dirs_exist_ok=True)
        shutil.copy2(src / "source.pdf", dst / "source.pdf")
        original = json.loads((src / "paper.json").read_text(encoding="utf-8"))
        original["blocks"] = []
        original["translation"] = {"done_pages": []}
        original.pop("reading", None)
        original.pop("reading_guide", None)
        write_json_atomic(dst / "paper.json", original)
        if (dst / "metrics.jsonl").exists():
            (dst / "metrics.jsonl").unlink()
        ws = Workspace(dst)
        cfg = dict(config.load(), reading_enabled=enabled, batch_pages=2, concurrency=1)
        start = time.monotonic()
        failed = translate.translate_pages(ws, cfg, [2, 3], threading.Event(),
                                            lambda d, t, m: print(dst.name, m, flush=True))
        rows = [json.loads(line) for line in (dst / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        results[dst.name] = {"failed": failed, "seconds": round(time.monotonic() - start, 2), "calls": len(rows),
                             "usage": {key: sum((r.get("usage") or {}).get(key, 0) for r in rows)
                                       for key in ("prompt_tokens", "completion_tokens", "total_tokens")}, "requests": rows}
        write_json_atomic(OUT / "ab-results.json", results)
        review(ws)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "enrich", "ab", "review"])
    args = parser.parse_args()
    if args.mode == "review":
        for pid in SAMPLES:
            review(Workspace(OUT / "library" / pid))
    else:
        globals()[args.mode]()

"""Opt-in same-model, fixed-translation old/new prompt comparison on two pages.

python tests/reading_semantic_ab.py --run
Consumes configured model quota. Calls only the reading batch (plus its existing
one repair if strict source validation fails), never synthesis or translation.
Both arms share current deterministic validation; no human verdicts are inferred.
"""
import argparse
import ast
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from easyread import config, engines, prompts, reading, translate
from easyread.store import Workspace, write_json_atomic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="explicitly run configured model")
    parser.add_argument("--reuse-old", type=Path, help="reuse an earlier identical-model baseline report")
    args = parser.parse_args()
    if not args.run:
        parser.error("use --run to execute the bounded two-page comparison")
    pid, pages = "f24268730b6e", [12, 19]
    source = ROOT / "artifacts" / "reading-qa" / "library" / pid
    raw = (source / "paper.json").read_bytes()
    original = json.loads(raw)
    assert set(pages) <= set(original["translation"]["done_pages"])
    baseline = subprocess.check_output(["git", "show", "2a4e321f241d966518fe045d6865af4f5ae608d7:easyread/prompts.py"], cwd=ROOT).decode("utf-8")
    old = {}
    for statement in ast.parse(baseline).body:
        if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("READING_RULES", "INSIGHTS_RULES") for t in statement.targets):
            old[statement.targets[0].id] = ast.literal_eval(statement.value)
    new = {name: getattr(prompts, name) for name in old}
    output = ROOT / "artifacts" / "reading-qa" / ("semantic-ab-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True)
    cfg = dict(config.load(), batch_pages=2, concurrency=1)
    reused = None
    if args.reuse_old:
        reused = json.loads(args.reuse_old.read_text(encoding="utf-8"))
        assert reused["model"] == engines.who(cfg) and reused["engine"] == cfg.get("engine")
        assert reused["paper"] == pid and reused["pages"] == pages
        assert not reused["arms"]["old"].get("error") and not reused["arms"]["old"].get("failed_pages")
    report = {"date": "2026-10-13", "model": engines.who(cfg), "engine": cfg.get("engine"),
              "paper": pid, "pages": pages, "temperature": "engine configured/default; not controlled",
              "comparison": "old/new prompts sharing current validator; fixed Chinese and source material",
              "sample_sha256": hashlib.sha256(raw).hexdigest(),
              "baseline_commit": "2a4e321f241d966518fe045d6865af4f5ae608d7", "human_quality_accepted": False, "arms": {}}
    try:
        for arm, rules in (("old", old), ("new", new)):
            if arm == "old" and reused:
                report["arms"]["old"] = copy.deepcopy(reused["arms"]["old"])
                report["arms"]["old"]["reused_from"] = str(args.reuse_old.resolve())
                shutil.copytree(args.reuse_old.parent / "old", output / "old")
                continue
            destination = output / arm
            destination.mkdir()
            shutil.copy2(source / "source.pdf", destination / "source.pdf")
            shutil.copytree(source / "extract", destination / "extract")
            paper = copy.deepcopy(original)
            paper["blocks"] = [b for b in paper["blocks"] if b.get("page") in pages]
            for block in paper["blocks"]:
                block.pop("highlights", None)
                block.pop("annotations", None)
            paper.pop("reading", None)
            paper.pop("reading_guide", None)
            paper["translation"].update(done_pages=pages, target_pages=pages, requested_scope="pages")
            write_json_atomic(destination / "paper.json", paper)
            before_blocks = copy.deepcopy(paper["blocks"])
            for name, value in rules.items():
                setattr(prompts, name, value)
            started = time.monotonic()
            error, bad = None, []
            print(arm, "start", report["model"], pages, flush=True)
            try:
                bad = sorted(translate._one_enrich(Workspace(destination), cfg, pages, threading.Event()))
            except Exception as exc:
                error = type(exc).__name__ + ": " + str(exc)[:200]
            result = Workspace(destination).load("paper")
            for block in result["blocks"]:
                plain = {k: v for k, v in block.items() if k not in ("highlights", "annotations")}
                assert plain == next(b for b in before_blocks if b["id"] == block["id"])
            highlights = [h for b in result["blocks"] for h in b.get("highlights") or []]
            counts = {cat: sum(h["category"] == cat for h in highlights) for cat in reading.CATEGORIES}
            metrics_path = destination / "metrics.jsonl"
            metrics = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines()] if metrics_path.exists() else []
            report["arms"][arm] = {"seconds": round(time.monotonic() - started, 3), "error": error,
                                   "failed_pages": bad, "counts": counts, "highlights": len(highlights),
                                   "scored": sum("importance" in h for h in highlights),
                                   "translation_unchanged": True, "model_calls": len(metrics),
                                   "prompt_sha256": hashlib.sha256("\n".join(rules.values()).encode()).hexdigest(),
                                   "metrics": metrics}
            write_json_atomic(output / "report.json", report)
            print(arm, json.dumps({k: v for k, v in report["arms"][arm].items() if k != "metrics"}, ensure_ascii=False), flush=True)
        assert (source / "paper.json").read_bytes() == raw
        print("report:", output / "report.json", flush=True)
    finally:
        for name, value in new.items():
            setattr(prompts, name, value)


if __name__ == "__main__":
    main()

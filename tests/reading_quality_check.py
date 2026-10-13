"""Cached real-paper quality comparison. No generation, credentials or library writes.

python tests/reading_quality_check.py
This measures deterministic postprocessing, not a new-prompt model A/B trial.
Human review fields intentionally start empty.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from easyread import reading, reading_quality

OUT = ROOT / "artifacts" / "reading-qa" / "semantic-v1"
SAMPLES = ("03112e8bdaeb", "f24268730b6e", "ca859378a829")


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    rows, review = [], []
    for pid in SAMPLES:
        src = ROOT / "artifacts" / "reading-qa" / "library" / pid / "paper.json"
        if not src.exists():
            rows.append({"paper": pid, "status": "missing_cached_sample"})
            continue
        raw = src.read_bytes()
        paper = json.loads(raw)
        before = copy.deepcopy(paper["blocks"])
        reading_quality.rebuild_quality(paper)
        assert before == paper["blocks"], "quality derivation mutated candidates/translations"
        quality = copy.deepcopy(paper["reading"]["quality"])
        reading_quality.rebuild_quality(paper)
        assert quality == paper["reading"]["quality"], "quality derivation is not idempotent"
        suppressed = {e["key"] for e in quality["suppressed"]}
        dump(OUT / "copies" / pid / "paper.json", paper)
        valid, total = 0, 0
        for b in paper["blocks"]:
            current = b.get("page") in paper.get("translation", {}).get("done_pages", []) and reading._state_current(paper, b.get("page"))
            for h in b.get("highlights") or []:
                total += 1
                key = reading_quality.highlight_stable_key(b["id"], h)
                usable = current and reading._locate_in_block(b, h.get("quote"), h.get("occurrence", 0)) is not None
                valid += usable
                if usable:
                    review.append({"paper": pid, "page": b["page"], "key": key,
                                   "category": h.get("category"), "quote": h.get("quote"),
                                   "importance": h.get("importance"), "zh": reading.block_zh(b),
                                   "source_en": reading._source_text(b), "suppressed": key in suppressed,
                                   "reviewer_a": {"worth_marking": None, "category": None, "duplicate": None,
                                                  "supported": None, "notes": ""},
                                   "reviewer_b": {"worth_marking": None, "category": None, "duplicate": None,
                                                  "supported": None, "notes": ""},
                                   "adjudication": None})
        assert src.read_bytes() == raw
        rows.append({"paper": pid, "sample_sha256": hashlib.sha256(raw).hexdigest(),
                     "analyzed_pages": sorted(p for p in paper["translation"]["done_pages"] if reading._state_current(paper, p)),
                     "stored_candidates_before": total, "stored_candidates_after": total,
                     "valid_anchors": valid, "confirmed_exact_repeats": len(suppressed),
                     "visible_with_quality_enabled": valid - len(suppressed),
                     "new_model_calls": 0, "translation_unchanged": True, "idempotent": True})
    # Add completed final-model candidates for the new categories without mixing
    # them into the cached before/after metrics.
    for report_path in sorted((ROOT / "artifacts" / "reading-qa").glob("semantic-ab-*/report.json"), reverse=True):
        model_report = json.loads(report_path.read_text(encoding="utf-8"))
        new = model_report.get("arms", {}).get("new", {})
        if new.get("error") or new.get("failed_pages"):
            continue
        from easyread import prompts
        current_prompt = hashlib.sha256((prompts.READING_RULES + "\n" + prompts.INSIGHTS_RULES).encode()).hexdigest()
        if new.get("prompt_sha256") != current_prompt:
            continue
        model_paper = json.loads((report_path.parent / "new" / "paper.json").read_text(encoding="utf-8"))
        for b in model_paper["blocks"]:
            for h in b.get("highlights") or []:
                review.append({"paper": model_report["paper"], "run": str(report_path.parent.name),
                               "page": b["page"], "key": reading_quality.highlight_stable_key(b["id"], h),
                               "category": h["category"], "quote": h["quote"], "importance": h.get("importance"),
                               "reason": h.get("reason"), "zh": reading.block_zh(b), "source_en": reading._source_text(b),
                               "reviewer_a": {"worth_marking": None, "category": None, "duplicate": None, "supported": None, "notes": ""},
                               "reviewer_b": {"worth_marking": None, "category": None, "duplicate": None, "supported": None, "notes": ""},
                               "adjudication": None})
        break
    report = {"date": "2026-10-13", "comparison": "same cached model output before/after deterministic quality",
              "new_prompt_ab_completed": False, "human_quality_accepted": False,
              "review_candidates": len(review), "samples": rows}
    dump(OUT / "cached-comparison.json", report)
    dump(OUT / "human-review.json", review)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

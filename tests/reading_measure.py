"""对真实精读产出做客观度量：数量、残句率、跨页重复率。不调用模型。

    python tests/reading_measure.py artifacts/reading-qa/verify-old artifacts/reading-qa/verify-ds

残句判定与语义无关：引文过短、结尾悬空（的/约/将/在/与/和/为/或），或数字被截断。
重复判定：去掉标点后两条高亮/要点跨页面互相包含，或字符三元组重合度不低于 0.8。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DANGLING = ("的", "约", "将", "在", "与", "和", "为", "或", "而", "使")
STRIP = re.compile(r"[\s，。；：、（）()·$\\]\[，。；：、（）]")


def _text(s):
    return STRIP.sub("", s or "")


def _grams(s, n=3):
    s = _text(s)
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def is_fragment(quote):
    q = (quote or "").strip()
    if len(q) < 12:  # 12 字以内承载不了一个完整主张
        return True
    if len(q) < 20 and q[-1] in DANGLING:
        return True
    if len(q) < 20 and (q.endswith("约") or re.search(r"\d+$", q)):
        return True
    return False


def duplicates(items):
    """items: [(id, page, text)]。返回跨页重复的 id。"""
    dup = set()
    for i, (iid, page, text) in enumerate(items):
        mine, grams = _text(text), _grams(text)
        if not mine:
            continue
        for jid, jpage, other in items[i + 1:]:
            if jpage == page:
                continue
            theirs = _text(other)
            if not theirs:
                continue
            if mine in theirs or theirs in mine:
                dup.update((iid, jid))
                continue
            both = grams | _grams(other)
            if both and len(grams & _grams(other)) / len(both) >= 0.8:
                dup.update((iid, jid))
    return dup


def measure(folder):
    paper = json.loads((Path(folder) / "paper.json").read_text(encoding="utf-8"))
    highlights, annotations = [], []
    for b in paper.get("blocks") or []:
        for h in b.get("highlights") or []:
            highlights.append((h.get("id"), b.get("page"), h.get("quote") or ""))
        for a in b.get("annotations") or []:
            annotations.append((a.get("id"), b.get("page"), a.get("content") or ""))
    insights = [(i.get("id"), i.get("page"), i.get("content") or "")
                for i in (paper.get("reading") or {}).get("insights") or []]
    frag = [i for i, _, q in highlights if is_fragment(q)]
    dup_h, dup_a, dup_i = duplicates(highlights), duplicates(annotations), duplicates(insights)
    return {"folder": str(folder),
            "blocks": len(paper.get("blocks") or []),
            "highlights": len(highlights), "highlight_fragments": len(frag),
            "highlight_fragment_ids": frag,
            "highlight_duplicates": len(dup_h), "highlight_duplicate_ids": sorted(dup_h),
            "annotations": len(annotations), "annotation_duplicates": len(dup_a),
            "insights": len(insights), "insight_duplicates": len(dup_i),
            "insight_duplicate_ids": sorted(dup_i)}


if __name__ == "__main__":
    for folder in sys.argv[1:]:
        print(json.dumps(measure(folder), ensure_ascii=False, indent=1))

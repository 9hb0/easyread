"""从本地提取结果建立原文块；不读写译文，也不调用模型。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .store import dir_lock, write_json_atomic

_SCHEMA = 1
_LINES = re.compile(r"[^\r\n\f]+")


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _characters(raw: bytes | None) -> list[tuple[str, list | None]]:
    try:
        entries = json.loads(raw) if raw else []
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    chars = []
    for entry in entries:
        if not isinstance(entry, list) or not entry or not isinstance(entry[0], str):
            continue
        box = entry[1:] if len(entry) == 5 else None
        if (box is not None and all(type(v) in (int, float) and math.isfinite(v) for v in box)
                and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            box = list(box)
        else:
            box = None
        chars.append((entry[0], box))
    return chars


def _compact(text: str) -> tuple[str, list[int]]:
    """保留大小写、标点、数学符号；空白/连字的转换始终带原位置映射。"""
    letters, positions = [], []
    for i, letter in enumerate(text):
        # 只展开排版连字；不能把公式的上/下标（²、₂）归一化成普通数字。
        expanded = unicodedata.normalize("NFKC", letter) if "\ufb00" <= letter <= "\ufb06" else letter
        for normalized in expanded:
            if not normalized.isspace():
                letters.append(normalized)
                positions.append(i)
    return "".join(letters), positions


def _stream(chars):
    letters, positions = [], []
    for i, (text, _) in enumerate(chars):
        compact, _ = _compact(text)
        letters.append(compact)
        positions.extend([i] * len(compact))
    return "".join(letters), positions


def _union(chars, positions) -> list | None:
    boxes = [chars[i][1] for i in set(positions)]
    if not boxes or any(box is None for box in boxes):
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _paragraph_break(previous: list, current: list) -> bool:
    height = max(previous[3] - previous[1], current[3] - current[1])
    return (current[1] - previous[3] > height * .65
            or current[1] < previous[1] - height * .5
            or current[0] - previous[0] > max(.012, height * .75))


def _character_text(chars) -> str:
    """缺 txt 时保留字符原顺序，按实际行位置恢复换行。"""
    parts, line = [], None
    for text, box in chars:
        if text.strip() and box is not None:
            if line is not None:
                height = max(line[3] - line[1], box[3] - box[1])
                if abs((line[1] + line[3]) - (box[1] + box[3])) > height * 1.4:
                    parts.append("\n\n" if _paragraph_break(line, box) else "\n")
                    line = None
            if line is None:
                line = box
            else:
                line = [min(line[0], box[0]), min(line[1], box[1]),
                        max(line[2], box[2]), max(line[3], box[3])]
        parts.append(text)
    return "".join(parts)


def _paragraphs(text: str, chars, char_stream: str, char_positions: list[int]):
    compact, raw_positions = _compact(text)
    aligned = bool(compact) and compact == char_stream
    # 只有整页完整匹配时才用几何信息区分软换行与段落边界。
    raw_to_char = dict(zip(raw_positions, char_positions)) if aligned else {}
    start, end, previous_box = None, None, None
    for line in _LINES.finditer(text):
        if not line.group().strip():
            continue
        line_positions = [raw_to_char[i] for i in range(line.start(), line.end()) if i in raw_to_char]
        box = _union(chars, line_positions) if aligned else None
        gap = text[end:line.start()] if end is not None else ""
        separate = bool(re.search(r"\n[\s\r]*\n|\f", gap))
        if previous_box is not None and box is not None:
            separate |= _paragraph_break(previous_box, box)
        if start is not None and separate:
            yield text[start:end].strip(), start, end
            start = None
        if start is None:
            start = line.start()
        end, previous_box = line.end(), box
    if start is not None:
        yield text[start:end].strip(), start, end


def _unique_match(stream: str, quote: str) -> int | None:
    start = stream.find(quote)
    if start < 0 or stream.find(quote, start + 1) >= 0:
        return None
    end = start + len(quote)
    # 不能把 Alpha 定位到 Alphabet 等较长词内部。
    if ((start and quote[0].isalnum() and stream[start - 1].isalnum())
            or (end < len(stream) and quote[-1].isalnum() and stream[end].isalnum())):
        return None
    return start


def _page(page: int, txt: bytes | None, raw_chars: bytes | None) -> dict:
    chars = _characters(raw_chars)
    text = (txt or b"").decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        text = _character_text(chars)
    char_stream, char_positions = _stream(chars)
    compact, raw_positions = _compact(text)
    aligned = bool(compact) and compact == char_stream
    paragraphs = list(_paragraphs(text, chars, char_stream, char_positions))
    counts = Counter(_compact(en)[0] for en, _, _ in paragraphs)
    blocks = []
    for index, (en, start, end) in enumerate(paragraphs, 1):
        quote, _ = _compact(en)
        box = None
        if quote and aligned:
            # 精确整页映射也能区分重复段落，不会反复使用第一个匹配位置。
            positions = [char_positions[i] for i, pos in enumerate(raw_positions) if start <= pos < end]
            box = _union(chars, positions)
        elif quote and counts[quote] == 1:
            match = _unique_match(char_stream, quote)
            if match is not None:
                box = _union(chars, char_positions[match:match + len(quote)])
        blocks.append({"id": f"p{page}-{index}", "page": page, "en": en, "box": box})
    digest = hashlib.sha256(f"sourceblocks:{_SCHEMA}\n".encode())
    for raw in (txt, raw_chars):
        digest.update(b"missing\n" if raw is None else str(len(raw)).encode() + b":" + raw)
    return {"source_version": digest.hexdigest(), "blocks": blocks}


def ensure(root: Path, pages) -> dict:
    """返回指定页的 blocks 和 pages[str(n)] = {source_version, blocks}。

    pages 接收页码或 prepare 返回的页面元数据（n 字段），按页码排序、去重。
    ID 为 p{page}-{index}（从 1 起）；原文/坐标变化通过独立 SHA-256 版本识别。
    extract/source-blocks.json 原子保存全部已处理页面，批次调用只返回所请求页面。
    无可靠完整匹配时 box 为 None，绝不填猜测区域。历史 paper.json 不受影响。
    """
    numbers = sorted({int(p["n"] if isinstance(p, dict) else p) for p in pages})
    if any(p < 1 for p in numbers):
        raise ValueError("页码必须从 1 开始")
    extract = Path(root) / "extract"
    extract.mkdir(parents=True, exist_ok=True)
    path = extract / "source-blocks.json"
    # 单独锁原文缓存，避免和 paper.json 的写锁形成嵌套。
    with dir_lock(extract, name=".source-blocks.lock"):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            saved = {}
        cached = saved.get("pages", {}) if isinstance(saved, dict) else {}
        if not isinstance(cached, dict):
            cached = {}
        cached = {k: v for k, v in cached.items()
                  if k.isdecimal() and int(k) > 0 and isinstance(v, dict) and isinstance(v.get("blocks"), list)}
        current = {}
        for n in numbers:
            base = extract / f"page-{n:03d}"
            current[str(n)] = _page(n, _read(base.with_suffix(".txt")), _read(base.with_suffix(".chars.json")))
        cached.update(current)
        cached = dict(sorted(cached.items(), key=lambda item: int(item[0])))
        output = {"blocks": [b for entry in cached.values() for b in entry["blocks"]], "pages": cached}
        if output != saved:
            write_json_atomic(path, output)
    return {"blocks": [b for entry in current.values() for b in entry["blocks"]], "pages": current}

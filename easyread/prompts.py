"""给模型的提示词：分批翻译、回答用户问题、重译一段。"""
from __future__ import annotations

import json

from .store import Workspace

RULES = """翻译要求：
- 忠实：保留原文的论证顺序、章节编号、公式、表格、引用号 [n]、限定词（may/suggest/likely/at least）、否定和比较对象。可以调整中文语序、拆长句，读起来要像中文母语者写的学术文字。
- 只翻译，不解释、不总结、不加原文没有的内容。原文的笔误照录，不要改。
- 但要留心原文自己的问题：数字前后对不上（表和正文、两张表之间）、公式和文字说的不一致、符号用错、明显的笔误。发现了就写进 checks，正文照录不改；没有就不写，不要为了写而写，也不要写翻译说明。
- 术语全文统一；首次出现的核心术语写“中文（English）”。已有术语表必须遵守。统计学里 standard error 译“标准误差”。
- 行内数学一律写成 $TeX$（KaTeX 能渲染的 LaTeX），变量、下标、上标都要用 TeX，不要用 Unicode 拼。行间公式单独成 math 块，照原页重排，原编号放 tag。
- 表格重排成 table 块，表头译成中文，数字原样。图用 figure 块，只写题注（src 留空）。
- 参考文献列表不翻译：输出一个 references 块，条目放进 references 数组（id 是编号，text 是原文）。
- 看不清的地方写“此处识别不清，请核对原文第 N 页”，不要猜。
- 页眉、页脚、页码、arXiv 侧边水印不要输出。"""

SCHEMA = """输出格式：只输出一个 JSON 对象，不要任何别的文字。
{
  "meta": {"title_zh": "", "short_zh": "不超过 12 字的短标题", "title_en": "", "authors": "作者, 用逗号分隔", "affiliation": "", "date": "", "venue": ""},   // 只有包含第 1 页时才写
  "glossary": [{"en": "standard error", "zh": "标准误差"}],   // 本批新出现的核心术语
  "references": [{"id": "1", "text": "原文条目"}],             // 本批出现参考文献列表时才写
  "checks": [{"anchor": "块 id", "quote": "译文里相关的几个字（可空）", "title": "一句话：哪里不对", "body": "具体说明和依据，比如算一遍给出对得上的数"}],   // 原文有问题时才写
  "blocks": [ ... ]
}
块（每块都要 id、type、page；page 是这块在原 PDF 中开始的页码）：
- {"id":"p3-2","type":"para","page":3,"en":"英文原文（行内数学也写成 $TeX$）","zh":"中文译文"}   摘要段落加 "role":"abstract"；紧接在公式后的半句（如 where …）加 "cont": true
- {"id":"s2-1","type":"heading","page":2,"level":1或2,"num":"2.1","en":"Independent questions","zh":"相互独立的题目"}   附录标题加 "appendix": true，摘要标题 num 留空
- {"id":"p2-5","type":"list","page":2,"ordered":true,"items":[{"en":"…","zh":"…"}]}
- {"id":"eq1","type":"math","page":3,"tex":"…","tag":"1"}   没有编号不写 tag；多行用 \\begin{aligned}…\\end{aligned}
- {"id":"tab2","type":"table","page":3,"num":"2","head":[["","题目数","…"]],"rows":[["MATH","5,000","65.5%\\n(0.7%)"]],"align":"lrr","caption_en":"Table 2: …","caption_zh":"表 2：…"}
- {"id":"fig1","type":"figure","page":4,"num":"1","src":"","caption_en":"Figure 1: …","caption_zh":"图 1：…"}
- {"id":"refs","type":"references","page":10,"zh":"参考文献","en":"References"}
id 规则：段落 p{页}-{序号}，标题 s{编号，点换成横线}，公式 eq{编号} 或 eq-p{页}-{序号}，表 tab{编号}，图 fig{编号}。
注意 JSON 里 TeX 的反斜杠要写两个（\\\\frac、\\\\text、\\\\bar）。字符串里的中文引号用“”或「」，不要出现没转义的英文双引号 "。表格和图放在正文第一次提到它的段落之后。"""


READING_RULES = """精读任务（和翻译一起完成）：
翻译每个文字块时，顺带给它标“语义高亮”——在该块上加 highlights 字段：
  "highlights": [{"category": "method", "quote": "该块 zh 译文里的一段连续原话", "occurrence": 0}]
- category 六选一：background 背景知识（理解论文必需的概念、定义、理论）；related_work 相关工作（别人已提出的方法、已有研究及其优缺点）；problem 解决的问题（研究动机、缺口、挑战、目标）；method 提出的方法（本文的技术方案、模型、算法、架构）；innovation 核心创新（与已有工作有实质差异的设计或贡献）；evidence 论据实验（支撑结论的实验数据、性能比较、证明）。
- quote 必须逐字来自该块最终的 zh 译文（连续子串，含标点），不得改写、不得跨块；occurrence 是这个字符串在该块译文里第几次出现，从 0 开始。
- 只标关键短语或关键句，优先短的；一段可以没有任何高亮；拿不准就不标，宁缺毋滥。
- 同一块内的高亮不要互相重叠；相关工作里的技术不是 innovation；作者声称的效果不等于 evidence；不要因为出现 novel/propose/significant 这类词就标创新或证据。
- 段落、标题、列表项、表格和图的中文题注都可以加 highlights；公式块不加。不得为了高亮改译文措辞。"""

ANNOTATION_RULES = """再给真正需要解释的块写少量精读批注——在该块上加 annotations 字段：
  "annotations": [{"type": "concept", "anchor_quote": "该块 zh 译文里的连续原话（留空则挂整块）", "content": "一到三句话的批注", "basis": "author_statement"}]
- type 七选一：concept 概念解释；method_explanation 方法怎么工作；design_rationale 为什么这样设计；formula 公式含义；experiment 实验说明；limitation 局限或前提；question 拿不准的疑点。
- basis 三选一：author_statement 作者明确说过；source_supported 论文里有依据的推断；ai_explanation 你补充的背景知识。不得把你的推断说成作者原意。
- 只给有必要解释的关键概念、方法、公式、实验和局限写批注；译文已经说清楚的不写；不逐句解释；一块最多两三条，整批少而精。
- 依据不足的写成 question 类型的疑问，不要硬下结论。批注不进入译文正文。"""

INSIGHTS_RULES = """最后提取本批出现的研究要点，在 JSON 顶层输出 insights：
  "insights": [{"type": "problem", "content": "一句话概括", "block": "本批某个块 id", "status": "provisional"}]
- type 七选一：background 背景；related_work 相关工作；problem 研究问题；method 方法；innovation 创新；evidence 证据；limitation 局限。
- 只提取高价值的：本批的核心研究问题、关键技术设计、实质创新点、实验结论、重要局限。一批 3 到 8 条，宁缺毋滥。
- block 必须是本批输出里的块 id，作为原文依据；content 用一句中文说清。
- status：provisional（暂定，默认）/ supported（本批内有直接证据）。早期页面的创新点一律 provisional，不要因为作者自称 novel 就标 supported。"""


def _context(ws: Workspace, pages: list[int]) -> str:
    paper = ws.load("paper")
    meta = paper.get("meta", {})
    blocks = paper.get("blocks", [])
    lines = [f"论文：{meta.get('title_en') or meta.get('source', '')}，共 {meta.get('page_count', '?')} 页。"]
    gl = paper.get("glossary", [])
    if gl:
        lines.append("已有术语表（必须沿用）：" + "；".join(f"{g['en']} = {g['zh']}" for g in gl))
    heads = [f"{b.get('num', '')} {b.get('zh', '')}".strip() for b in blocks if b.get("type") == "heading"]
    if heads:
        lines.append("已译的章节：" + " / ".join(heads))
    ids = [b["id"] for b in blocks]
    if ids:
        lines.append("已用过的块 id（不要重复）：" + ", ".join(ids[-60:]))
    prev = next((b for b in reversed(blocks) if (b.get("page") or 0) < pages[0] and b.get("en")), None)
    if prev:
        lines.append(f"上一批最后一段（{prev['id']}，第 {prev['page']} 页）的英文结尾：……{prev['en'][-300:]}\n"
                     "如果本批第一页开头是这一段的续文，不要再输出这段续文。")
    return "\n".join(lines)


def translate(ws: Workspace, pages: list[int], engine: str, next_head: str, reading: bool = False) -> str:
    texts = []
    for n in pages:
        p = ws.root / "extract" / f"page-{n:03d}.txt"
        texts.append(f"===== 第 {n} 页（抽取的文字，公式和表格可能是乱的）=====\n" + (p.read_text(encoding="utf-8") if p.exists() else ""))
    look = ""
    if next_head:
        look = ("\n===== 下一页开头（只用来把本批最后一段补完整，其余不要翻译）=====\n" + next_head)
    see = ""
    if engine == "claude":
        imgs = "、".join(f"extract/page-{n:03d}.jpg" for n in pages)
        see = f"\n先用 Read 工具看原页图 {imgs}，以原页为准核对公式、表格、上下标和阅读顺序（双栏论文按栏读）。抽取的文字只作参考。"
    elif engine == "attached":
        see = "\n附上了这几页的原页图，以原页为准核对公式、表格和阅读顺序。"
    rules = RULES + ("\n\n" + READING_RULES + "\n\n" + ANNOTATION_RULES + "\n\n" + INSIGHTS_RULES if reading else "")
    return (f"你在把一篇学术论文译成中文，这次只处理第 {', '.join(map(str, pages))} 页。{see}\n\n"
            f"{_context(ws, pages)}\n\n{rules}\n\n{SCHEMA}\n\n" + "\n\n".join(texts) + look)


def repair(original_json: str, problems: list[str]) -> str:
    return ("下面这份论文翻译 JSON 有问题，请修好后输出完整的 JSON（格式不变，只输出 JSON）：\n"
            + "\n".join(f"- {p}" for p in problems[:30]) + "\n\nJSON：\n" + original_json)


def enrich(ws: Workspace, pages: list[int]) -> str:
    """已翻译页面的“仅补精读”：给模型看现有译文，只要精读标注，不许改译文。"""
    paper = ws.load("paper")
    targets = [b for b in paper.get("blocks", []) if b.get("page") in pages]
    lines = []
    for b in targets:
        zh = b.get("zh") or b.get("caption_zh") or ""
        if not zh:
            continue
        items = "\n".join("- " + it.get("zh", "") for it in b.get("items", [])) if b.get("items") else ""
        text = items or zh
        lines.append(f"[{b['id']}] {text}")
    rules = READING_RULES + "\n\n" + ANNOTATION_RULES + "\n\n" + INSIGHTS_RULES
    return ("下面是论文《" + (paper.get("meta", {}).get("title_zh") or paper.get("meta", {}).get("title_en") or "") + "》第 "
            + "、".join(map(str, pages)) + " 页的中文译文（按块 id）。译文已定稿，只做精读标注，不许改译文。\n\n"
            + rules + "\n\n"
            + "只输出一个 JSON 对象，不要别的文字：\n"
            + '{"blocks": [{"id": "上面列出的块 id", "highlights": [每条 {category, quote, occurrence}], "annotations": [每条 {type, anchor_quote, content, basis}]}]}\n'
            + "只列需要标注的块；两样都没有的块不用列。insights 照常在顶层输出。\n\n译文：\n" + "\n\n".join(lines))


def synthesize(ws: Workspace) -> str:
    """全文译完后的一次研究导航综合：浓缩 insights，建跨章节关系，核验结论。"""
    paper = ws.load("paper")
    insights = (paper.get("reading") or {}).get("insights") or []
    lines = [f"[{i['id']}]（{i.get('type')}/{i.get('status')}，第 {i.get('page')} 页 {i.get('block')}）{i.get('content')}" for i in insights]
    return ("下面是翻译《" + (paper.get("meta", {}).get("title_zh") or paper.get("meta", {}).get("title_en") or "这篇论文") +
            "》时逐批提取的研究要点（带 id 和原文出处）。请整合成研究导航：\n\n"
            "1. summary：一句话说清这篇论文解决什么问题、用什么方法、给出什么证据（不超过 80 字）。\n"
            '2. relations：3 到 8 条跨章节逻辑关系，比如“研究问题 A 由方法 B 解决”“方法 B 的效果由实验 C 验证”，每条 {"from": "要点 id", "to": "要点 id", "content": "一句话"}。\n'
            '3. checks：核验每个创新点——论文里有实验或论证直接支撑的标 supported，只声称没验证的标 provisional，找不到依据或与原文不符的标 unsupported，每条 {"insight": "要点 id", "status": "supported|provisional|unsupported"}。\n\n'
            "只能引用上面给出的 id，不得编造要点；关系必须有依据，拿不准就不写。\n"
            '只输出 JSON：{"summary": "", "relations": [], "checks": []}\n\n研究要点：\n' + "\n".join(lines))


def _block_text(b: dict) -> str:
    if b.get("type") == "list":
        return "\n".join(f"- {it.get('zh', '')}" for it in b.get("items", []))
    if b.get("type") in ("table", "figure"):
        return b.get("caption_zh", "")
    if b.get("type") == "math":
        return f"$${b.get('tex', '')}$$"
    return b.get("zh", "")


def answer(ws: Workspace, note: dict) -> str:
    paper = ws.load("paper")
    blocks = paper.get("blocks", [])
    idx = next((i for i, b in enumerate(blocks) if b.get("id") == note.get("anchor")), None)
    near = blocks[max(0, idx - 3): idx + 3] if idx is not None else blocks[:6]
    section = ""
    if idx is not None:
        h = next((b for b in reversed(blocks[:idx + 1]) if b.get("type") == "heading"), None)
        section = f"{h.get('num', '')} {h.get('zh', '')}" if h else ""
    ctx = "\n\n".join(f"[{b['id']}] {_block_text(b)}" for b in near)
    focus = blocks[idx] if idx is not None else {}
    return (f"你在和读者一起读论文《{paper.get('meta', {}).get('title_zh') or paper.get('meta', {}).get('title_en')}》。"
            f"读者读到「{section}」时在 [{note.get('anchor')}] 这段提了一个问题。\n\n"
            f"上下文（中文译文）：\n{ctx}\n\n这段英文原文：{focus.get('en', '')}\n\n"
            + (f"读者选中的原话：「{note.get('quote')}」\n" if note.get("quote") else "")
            + f"读者的问题：{note.get('body', '')}\n\n"
            "需要时可以用 Read 读当前目录的 paper.json 看全文。请直接回答：用中文，具体、讲清楚，能举例就举例，"
            "区分“论文里写了什么”和“你的补充解释”。行内公式用 $TeX$，段落之间空一行。只输出回答正文，不要客套。")


def retranslate(ws: Workspace, key: str, hint: str) -> str:
    paper = ws.load("paper")
    bid, _, field = key.partition("#")
    blocks = paper.get("blocks", [])
    idx = next(i for i, b in enumerate(blocks) if b.get("id") == bid)
    b = blocks[idx]
    if field == "caption":
        en, zh = b.get("caption_en", ""), b.get("caption_zh", "")
    elif field.isdigit():
        en, zh = b["items"][int(field)].get("en", ""), b["items"][int(field)].get("zh", "")
    else:
        en, zh = b.get("en", ""), b.get("zh", "")
    near = "\n".join(_block_text(x) for x in blocks[max(0, idx - 2): idx + 3] if x is not b)
    gl = "；".join(f"{g['en']} = {g['zh']}" for g in paper.get("glossary", []))
    return (f"请重新翻译论文里的一段。\n{RULES}\n\n术语表：{gl}\n\n前后文（译文）：\n{near}\n\n"
            f"英文原文：\n{en}\n\n现在的译文：\n{zh}\n\n"
            + (f"读者觉得不好的地方：{hint}\n\n" if hint else "")
            + '只输出 JSON：{"zh": "新译文"}')


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)

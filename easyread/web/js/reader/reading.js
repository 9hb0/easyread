/* 八类精读。先校验页面绑定，再把保存的 quote 精确映射到译文文本节点。
   Markdown 保留原始位置；公式、引用链接和列表分隔符不包裹。 */
(function (PR) {
  "use strict";
  const S = PR.state;
  PR.READING_VERSION = 2;
  PR.READING_CATS = [
    ["background", "背景知识"], ["related_work", "相关工作"], ["problem", "解决的问题"],
    ["method", "提出的方法"], ["innovation", "核心创新"], ["evidence", "论据实验"],
    ["finding", "关键结论"], ["limitation", "局限性"],
  ];
  const CAT = Object.fromEntries(PR.READING_CATS);
  const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  PR.readingCatName = (id) => own(CAT, id) ? CAT[id] : "";
  PR.READING_ANN_TYPES = {
    concept: "概念", method_explanation: "方法解释", design_rationale: "设计动机", formula: "公式",
    experiment: "实验", limitation: "局限", question: "疑问",
  };
  PR.READING_ANN_BASES = {
    author_statement: "作者陈述", source_supported: "有原文依据", ai_explanation: "AI 解释",
  };
  const array = (v) => Array.isArray(v) ? v : [];
  const blocks = () => array((S.paper || {}).blocks);
  const pageNumbers = (v) => [...new Set(array(v).map(Number).filter((n) => Number.isInteger(n) && n > 0))].sort((a, b) => a - b);
  PR.readingTranslatedPages = () => pageNumbers(((S.paper || {}).translation || {}).done_pages);

  function units(b, edited) {
    const value = (key, raw) => {
      const e = ((S.reader || {}).edits || {})[key];
      return String(edited && e && e.zh != null ? e.zh : raw || "");
    };
    if (b.type === "list") return array(b.items).map((i, n) => ({ key: b.id + "#" + n, text: value(b.id + "#" + n, i.zh) }));
    if (b.type === "figure" || b.type === "table") return [{ key: b.id + "#caption", text: value(b.id + "#caption", b.caption_zh) }];
    if (["para", "heading", "references", "note"].includes(b.type)) return [{ key: b.id, text: value(b.id, b.zh) }];
    return [];
  }
  const blockText = (b, edited) => units(b, edited).map((u) => u.text).join("\n");

  function cellText(value) {
    if (value == null) return "";
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  }

  function sourceText(b) {
    if (own(b, "source_en")) return typeof b.source_en === "string" ? b.source_en : "";
    if (b.type === "list") return array(b.items).map((i) => cellText(i.en)).join("\n");
    const value = ["table", "figure"].includes(b.type) ? b.caption_en : b.en;
    return typeof value === "string" ? value : "";
  }

  function contentHash(page) {
    const records = blocks().filter((b) => Number(b.page) === Number(page)).map((b) => {
      const fields = [cellText(b.id), cellText(b.type), blockText(b, true), cellText(b.tex),
        cellText(b.tag), sourceText(b)];
      if (b.type === "table") {
        for (const row of [...array(b.head), ...array(b.rows)]) fields.push(array(row).map(cellText).join("\u241f"));
      }
      return fields.join("\u241f");
    });
    return PR.hashText(records.join("\u241e"));
  }
  PR.readingContentHash = contentHash;
  const importance = (h) => Number.isInteger(h.importance) && h.importance >= 1 && h.importance <= 5 ? h.importance : null;
  const DENSITIES = [["lite", "精简"], ["standard", "标准"], ["deep", "深度"]];
  const densityValid = (v) => DENSITIES.some(([id]) => id === v);
  const highlightKey = (id, h) => [id, h.category, h.occurrence ?? 0, PR.hashText(h.quote)].join("|");
  function zhHash(page) {
    return PR.hashText(blocks().filter((b) => Number(b.page) === Number(page)).map((b) => blockText(b, true)).filter(Boolean).join("\n"));
  }
  PR.readingQualityInputHash = function () {
    const p = S.paper || {}, tr = p.translation || {}, rd = p.reading || {}, done = tr.done_pages || [];
    const pages = [...new Set([...blocks().map((b) => b.page), ...array(done)].filter(Number.isInteger))].sort((a, b) => a - b);
    const pageRows = pages.map((page) => {
      const st = (rd.pages || {})[page] || {};
      return [page, (tr.source_versions || {})[page] ?? null, st.status ?? null, st.version ?? null,
        st.zh_hash ?? null, st.content_hash ?? null, st.source_version ?? null, st.prompt_version ?? null,
        zhHash(page), contentHash(page)];
    });
    const candidateRows = [];
    for (const b of blocks()) for (const h of array(b.highlights)) {
      if (!h || typeof h !== "object" || Array.isArray(h)) continue;
      candidateRows.push([b.id ?? null, b.page ?? null, highlightKey(b.id || "", h), h.category ?? null,
        h.quote ?? null, h.occurrence ?? 0, importance(h), b.type ?? null, b.role ?? null, h.block_id ?? null,
        own(h, "source_refs") ? [true, h.source_refs] : [false, null]]);
    }
    return PR.hashText(JSON.stringify([PR.READING_VERSION, 1, 1, rd.version ?? null, done, pageRows, candidateRows]));
  };
  function qualitySuppressed() {
    const q = ((S.paper || {}).reading || {}).quality;
    // 每次显示重新验证输入；不复用上一次 apply 的旧哈希或抑制集合。
    if (!q || q.version !== 1 || typeof q.input_hash !== "string" || q.input_hash !== PR.readingQualityInputHash()) return new Set();
    // A suppressed candidate must still have a visible, valid representative in
    // the current density/category view. Legacy unrated items may outlive a
    // low-scored canonical; DOM/source validation can also hide that canonical.
    const visible = new Set(), filter = { ...PR.readingFilter, quality: false };
    for (const b of blocks()) for (const { h } of dataFor(b).highlights) {
      if (PR.shouldShowHighlight(h, filter, null)) visible.add(highlightKey(h.block_id, h));
    }
    return new Set(array(q.suppressed).filter((r) => r && r.reason === "exact_duplicate" &&
      typeof r.key === "string" && typeof r.canonical_key === "string" && r.key !== r.canonical_key &&
      visible.has(r.canonical_key)).map((r) => r.key));
  }
  PR.shouldShowHighlight = function (h, f, suppressed) {
    if (f.off || (f.only && f.only !== h.category)) return false;
    const score = importance(h), threshold = f.density === "lite" ? 5 : f.density === "deep" ? 2 : 4;
    if (score !== null && score < threshold) return false;
    return !f.quality || f.showSuppressed || !suppressed || !suppressed.has(highlightKey(h.block_id, h));
  };

  PR.readingPageValid = function (page) {
    const p = S.paper || {}, tr = p.translation || {}, reading = p.reading || {};
    const n = Number(page), st = (reading.pages || {})[n] || {};
    if (!Number.isInteger(n) || !PR.readingTranslatedPages().includes(n) || reading.version !== PR.READING_VERSION ||
        st.status !== "completed" || st.version !== PR.READING_VERSION) return false;
    const version = (tr.source_versions || {})[n];
    if (typeof version !== "string" || !version || st.source_version !== version || !st.zh_hash ||
        typeof st.content_hash !== "string" || !st.content_hash) return false;
    const text = blocks().filter((b) => Number(b.page) === n).map((b) => blockText(b, true)).filter(Boolean).join("\n");
    return st.zh_hash === PR.hashText(text) && st.content_hash === contentHash(n);
  };

  /* 引用必须真实存在且属于同一页；不从条目内容或块顺序猜出处。 */
  PR.readingSources = function (item) {
    const refs = array(item && item.source_refs);
    if (!refs.length) return [];
    const out = [], seen = new Set();
    for (const r of refs) {
      if (!r || typeof r !== "object") return [];
      const id = r.block_id || r.block, n = Number(r.page);
      if (r.block_id != null && r.block != null && r.block_id !== r.block) return [];
      const b = blocks().find((b) => b.id === id);
      if (!b || Number(b.page) !== n || !PR.readingPageValid(n) ||
          (r.source_version && r.source_version !== (((S.paper || {}).translation || {}).source_versions || {})[n])) return [];
      const original = own(b, "source_en") ? b.source_en : b.en || b.caption_en || array(b.items).map((i) => i.en || "").join("\n");
      if (typeof original !== "string" || !original.trim()) return [];
      const quoteKeys = ["source_quote", "quote"].filter((key) => own(r, key));
      if (quoteKeys.some((key) => typeof r[key] !== "string" || !r[key].trim() || !original.includes(r[key])) ||
          (quoteKeys.length === 2 && r.source_quote !== r.quote)) return [];
      const key = n + ":" + id;
      if (!seen.has(key)) { seen.add(key); out.push({ page: n, block_id: id }); }
    }
    return out;
  };
  PR.readingSourceHtml = function (item) {
    return PR.readingSources(item).map((r) => '<button type="button" class="reading-source" data-reading-source="' +
      PR.esc(r.block_id) + '" data-page="' + r.page + '" title="对照原文第 ' + r.page + ' 页">原文 p.' + r.page + "</button>").join("");
  };

  const OPAQUE = "\uFFFC";
  const MATH = /(?<!\\)\$((?:\\\$|[^$])+?)(?<!\\)\$/g;
  const MATH_NODE = ".katex, .katex-display, .katex-error, math, .MathJax, .math-body, code[title='公式渲染失败']";
  const SKIP = ".num, .en-title, .stale-tag, button, textarea, .editor-wrap, script, style";

  /* 每个投影字符都带着原始 UTF-16 区间；只删语法符号，不折叠空白。 */
  function rewrite(tokens, re, replacement) {
    const text = tokens.map((t) => t.text).join(""), out = [];
    let last = 0;
    re.lastIndex = 0;
    for (const m of text.matchAll(re)) {
      out.push(...tokens.slice(last, m.index));
      out.push(...replacement(tokens.slice(m.index, m.index + m[0].length), m));
      last = m.index + m[0].length;
    }
    out.push(...tokens.slice(last));
    return out;
  }
  function projection(raw) {
    let ts = raw.split("").map((text, i) => ({ text, start: i, end: i + 1 }));
    ts = rewrite(ts, MATH, (part) => [{ text: OPAQUE, start: part[0].start, end: part[part.length - 1].end, opaque: true }]);
    ts = rewrite(ts, /\\\$/g, (part) => [{ text: "$", start: part[0].start, end: part[1].end }]);
    // markup.js 会把已有块编号换成页码/公式标签；这段生成文字不可拆开定位。
    ts = rewrite(ts, /\[([a-z]+\d*(?:-[\w-]+)?)\]/g, (part, m) => {
      const b = PR.blockById && PR.blockById[m[1]];
      if (!b) return part;
      const label = b.type === "math" && b.tag ? "（式 " + b.tag + "）" : b.page ? "（第 " + b.page + " 页）" : m[0];
      return label.split("").map((text) => ({ text, start: part[0].start, end: part[part.length - 1].end, opaque: true }));
    });
    ts = rewrite(ts, /`([^`\n]+)`/g, (part) => part.slice(1, -1));
    ts = rewrite(ts, /\*\*([^*\n]+)\*\*/g, (part) => part.slice(2, -2));
    return rewrite(ts, /(^|[^*])\*([^*\n]+)\*(?!\*)/g, (part, m) =>
      [...part.slice(0, m[1].length), ...part.slice(m[1].length + 1, -1)]);
  }
  function domTokens(root) {
    const ts = [];
    function walk(node, interactive) {
      if (node.nodeType === Node.TEXT_NODE) {
        for (let i = 0; i < node.data.length; i++) ts.push({ text: node.data[i], node, offset: i, opaque: interactive });
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (node !== root && node.matches(SKIP)) return;
        if (node.matches(MATH_NODE)) { ts.push({ text: OPAQUE, opaque: true }); return; }
        if (node.tagName === "BR") { ts.push({ text: "\n", opaque: true }); return; }
        for (const child of node.childNodes) walk(child, interactive || node.tagName === "A");
      }
    }
    walk(root, false);
    return ts;
  }
  function sourceMap(b) {
    const host = document.getElementById("b-" + b.id);
    if (!host) return null;
    const mapped = [], parts = units(b, false);
    let raw = "";
    for (let i = 0; i < parts.length; i++) {
      const u = parts[i], zh = PR.$$(".zh[data-key]", host).find((el) => el.dataset.key === u.key);
      if (!zh || zh.querySelector("textarea, [contenteditable=true]")) return null;
      const projected = projection(u.text), rendered = domTokens(zh);
      if (projected.map((t) => t.text).join("") !== rendered.map((t) => t.text).join("")) return null;
      if (i) raw += "\n";
      const base = raw.length;
      for (let j = 0; j < projected.length; j++) {
        const p = projected[j], d = rendered[j];
        mapped.push({ start: base + p.start, end: base + p.end, node: d.node, offset: d.offset, opaque: p.opaque || d.opaque });
      }
      raw += u.text;
    }
    return { raw, mapped };
  }
  function locate(map, quote, occurrence) {
    if (!map || typeof quote !== "string" || !quote || !Number.isInteger(occurrence) || occurrence < 0) return null;
    let start = -1;
    for (let i = 0; i <= occurrence; i++) { start = map.raw.indexOf(quote, start + 1); if (start < 0) return null; }
    const end = start + quote.length;
    const ts = map.mapped.filter((t) => t.start < end && t.end > start);
    if (!ts.length || ts.some((t) => t.start < start || t.end > end)) return null;
    return { start, end, tokens: ts };
  }
  function dataFor(b) {
    const empty = { highlights: [], annotations: [] };
    if (!b || !PR.readingPageValid(b.page)) return empty;
    const map = sourceMap(b);
    if (!map) return empty;
    const candidates = [], seen = new Set();
    for (const saved of array(b.highlights)) {
      if (!saved || saved.block_id !== b.id || !own(CAT, saved.category)) continue;
      // 后端高亮以所属 block_id 绑定；派生出处仅用于本次显示，不改写保存数据。
      const h = own(saved, "source_refs") ? saved : { ...saved, source_refs: [{ page: b.page, block: b.id }] };
      if (!PR.readingSources(h).length) continue;
      const range = locate(map, h.quote, h.occurrence == null ? 0 : h.occurrence);
      if (!range || !range.tokens.some((t) => t.node && !t.opaque)) continue;
      const key = range.start + ":" + range.end + ":" + h.category;
      if (!seen.has(key)) { seen.add(key); candidates.push({ h, range }); }
    }
    // 所有冲突双方都不显示，避免按数组顺序选中错误类别。
    const highlights = candidates.filter((a) => !candidates.some((b) => a !== b && a.range.start < b.range.end && b.range.start < a.range.end));
    const annotations = [];
    for (const a of array(b.annotations)) {
      if (!a || a.block_id !== b.id || !own(PR.READING_ANN_TYPES, a.type) || !own(PR.READING_ANN_BASES, a.basis) ||
          typeof a.content !== "string" || !a.content.trim() || !PR.readingSources(a).length) continue;
      const range = locate(map, a.anchor_quote, a.occurrence == null ? (a.anchor_occurrence == null ? 0 : a.anchor_occurrence) : a.occurrence);
      if (range) annotations.push({ a, range });
    }
    return { highlights, annotations };
  }
  PR.readingAnnotations = (b) => dataFor(b).annotations.map((it) => it.a);
  PR.readingMarksForAnnotation = function (block, id) {
    const b = blocks().find((b) => b.id === block), ann = dataFor(b).annotations.find((it) => it.a.id === id);
    if (!ann) return [];
    return PR.$$("#paper mark.sem").filter((m) => m.dataset.block === block && Number(m.dataset.start) < ann.range.end && ann.range.start < Number(m.dataset.end));
  };
  PR.readingPagesNeeding = () => PR.readingTranslatedPages().filter((n) => !PR.readingPageValid(n));
  PR.hasReadingMarks = () => blocks().some((b) => dataFor(b).highlights.length);

  const saved = PR.ls.get("pr-reading-filter", {});
  PR.readingFilter = { off: !!(saved && saved.off), only: saved && own(CAT, saved.only) ? saved.only : "",
    density: saved && densityValid(saved.density) ? saved.density : "standard",
    quality: !!(saved && saved.quality === true), showSuppressed: !!(saved && saved.showSuppressed === true) };
  let opener = null, enrichPending = false;
  const readingPop = () => { const p = PR.$("#popover"); return p && p.classList.contains("open") && p.querySelector("[data-reading-close]") ? p : null; };
  function closePop(focus) {
    if (!readingPop()) return;
    PR.hidePopover();
    const pop = PR.$("#popover"); pop.removeAttribute("role"); pop.removeAttribute("aria-label");
    if (opener) opener.setAttribute("aria-expanded", "false");
    if (focus) (opener && opener.isConnected ? opener : PR.$('[data-act="reading"]'))?.focus({ preventScroll: true });
    opener = null;
  }
  function openPop(anchor, html, label, selector) {
    if (opener && opener !== anchor) opener.setAttribute("aria-expanded", "false");
    opener = anchor;
    PR.popover(anchor, html + '<button type="button" class="reading-close" data-reading-close aria-label="关闭">×</button>', { sticky: true });
    const p = PR.$("#popover"); p.setAttribute("role", "dialog"); p.setAttribute("aria-label", label);
    anchor.setAttribute("aria-expanded", "true"); anchor.setAttribute("aria-controls", "popover");
    const r = p.getBoundingClientRect();
    p.style.left = Math.max(8, Math.min(innerWidth - r.width - 8, r.left)) + "px";
    p.style.top = Math.max(58, Math.min(innerHeight - r.height - 8, r.top)) + "px";
    (PR.$(selector || "button", p) || p).focus({ preventScroll: true });
  }
  function unwrap(scope) {
    for (const m of PR.$$("mark.sem", scope)) m.replaceWith(...m.childNodes);
  }
  function wrapPlans(plans) {
    const byNode = new Map();
    for (const { h, range, block } of plans) {
      let last = null;
      for (const t of range.tokens) {
        if (!t.node || t.opaque) { last = null; continue; }
        if (last && last.node === t.node && last.end === t.offset) last.end++;
        else {
          last = { node: t.node, start: t.offset, end: t.offset + 1, h, block, range };
          if (!byNode.has(t.node)) byNode.set(t.node, []);
          byNode.get(t.node).push(last);
        }
      }
    }
    const first = new Map();
    for (const [text, pieces] of byNode) {
      for (const p of pieces.sort((a, b) => b.start - a.start)) {
        let node = text;
        if (p.end < node.data.length) node.splitText(p.end);
        if (p.start) node = node.splitText(p.start);
        const m = document.createElement("mark"), id = String(p.h.id || "h-" + PR.hashText(p.block + ":" + p.range.start + ":" + p.h.category));
        m.className = "sem cat-" + p.h.category;
        m.dataset.cat = p.h.category; m.dataset.hl = id; m.dataset.block = p.block;
        m.dataset.start = p.range.start; m.dataset.end = p.range.end;
        m.title = CAT[p.h.category] + " · 查看精读与原文";
        m.setAttribute("role", "button"); m.setAttribute("aria-label", m.title); m.tabIndex = -1;
        node.parentNode.insertBefore(m, node); m.appendChild(node);
        const key = p.block + ":" + id;
        const prev = first.get(key);
        if (!prev || (m.compareDocumentPosition(prev) & Node.DOCUMENT_POSITION_FOLLOWING)) first.set(key, m);
      }
    }
    for (const m of first.values()) m.tabIndex = 0;
  }
  PR.applyReadingMarks = function (onlyBlock) {
    const scope = onlyBlock ? document.getElementById("b-" + onlyBlock) : PR.$("#paper");
    if (!scope) return;
    const popupMark = readingPop() && opener && opener.matches("mark.sem") ? opener : null;
    const focused = popupMark || (scope.contains(document.activeElement) && document.activeElement.matches("mark.sem") ? document.activeElement : null);
    const focusAt = focused && { block: focused.dataset.block, start: focused.dataset.start, cat: focused.dataset.cat };
    if (popupMark) closePop(false);
    unwrap(scope);
    if (PR.readingFilter.off) { if (focusAt) PR.$('[data-act="reading"]')?.focus({ preventScroll: true }); return; }
    const plans = [], suppressed = PR.readingFilter.quality ? qualitySuppressed() : null;
    for (const b of blocks()) {
      if (onlyBlock && b.id !== onlyBlock) continue;
      for (const p of dataFor(b).highlights) if (PR.shouldShowHighlight(p.h, PR.readingFilter, suppressed)) plans.push({ ...p, block: b.id });
    }
    wrapPlans(plans);
    if (focusAt) (PR.$$("mark.sem", scope).find((m) => m.dataset.block === focusAt.block && m.dataset.start === focusAt.start && m.dataset.cat === focusAt.cat) ||
      PR.$('[data-act="reading"]'))?.focus({ preventScroll: true });
  };
  PR.saveReadingFilter = function (f) {
    PR.readingFilter = { off: f.off == null ? PR.readingFilter.off : !!f.off,
      only: f.only == null ? PR.readingFilter.only : own(CAT, f.only) ? f.only : "",
      density: f.density == null ? PR.readingFilter.density : densityValid(f.density) ? f.density : "standard",
      quality: f.quality == null ? PR.readingFilter.quality : f.quality === true,
      showSuppressed: f.showSuppressed == null ? PR.readingFilter.showSuppressed : f.showSuppressed === true };
    PR.ls.set("pr-reading-filter", PR.readingFilter);
    PR.applyReadingMarks();
  };

  PR.toggleReadingFilter = function (anchor, focusSelector) {
    const f = PR.readingFilter, translated = PR.readingTranslatedPages();
    const c = {}, shown = {}, suppressed = f.quality ? qualitySuppressed() : null;
    let total = 0, visible = 0, unrated = 0;
    for (const b of blocks()) for (const { h } of dataFor(b).highlights) {
      total++; c[h.category] = (c[h.category] || 0) + 1;
      if (importance(h) === null) unrated++;
      if (PR.shouldShowHighlight(h, f, suppressed)) { visible++; shown[h.category] = (shown[h.category] || 0) + 1; }
    }
    const rows = PR.READING_CATS.map(([id, name]) => '<button type="button" class="rf-row' + (!f.off && f.only === id ? " on" : "") +
      '" data-cat="' + id + '" aria-pressed="' + (!f.off && f.only === id) + '"' +
      (id === "limitation" ? ' title="作者明确陈述的局限性"' : "") + '><i class="dot cat-' + id +
      '"></i><span>' + name + '</span><b>' + (shown[id] || 0) + " / " + (c[id] || 0) + "</b></button>").join("");
    const density = DENSITIES.map(([id, name]) => '<button type="button" class="btn sm line' + (f.density === id ? " on" : "") +
      '" data-density="' + id + '" aria-pressed="' + (f.density === id) + '">' + name + "</button>").join("");
    openPop(anchor, '<div class="hd">语义高亮</div><div class="rf-density" role="group" aria-label="高亮密度">' + density +
      '</div><div class="rf-summary">当前显示 ' + visible + " / 全部有效候选 " + total + "</div>" +
      (unrated ? '<div class="rf-hint">' + unrated + " 条旧标注未评级，各档保留；可手动精读更新。</div>" : "") +
      '<div class="rf-list">' + rows + '</div><div class="rf-quality">' +
      '<button type="button" class="btn sm line' + (f.quality ? " on" : "") + '" data-f="quality" aria-pressed="' + f.quality + '">重复质控（试验）</button>' +
      '<button type="button" class="btn sm line' + (f.showSuppressed ? " on" : "") + '" data-f="showSuppressed" aria-pressed="' + f.showSuppressed + '">显示重复项</button>' +
      '<small>尚未人工验收</small></div><div class="rf-btns">' +
      '<button type="button" class="btn sm line" data-f="all">显示全部</button>' +
      '<button type="button" class="btn sm line" data-f="off">' + (f.off ? "开启高亮" : "关闭高亮") + "</button>" +
      (PR.store.mode === "server" && translated.length ? '<button type="button" class="btn sm line" data-f="enrich"' +
        (enrichPending ? " disabled" : "") + '>手动精读全部 ' + translated.length + " 页</button>" : "") + "</div>", "语义高亮筛选", focusSelector);
    PR.$("#popover").onclick = (ev) => {
      if (ev.target.closest("[data-reading-close]")) { closePop(true); return; }
      const row = ev.target.closest(".rf-row[data-cat]");
      if (row) {
        PR.saveReadingFilter({ only: f.only === row.dataset.cat && !f.off ? "" : row.dataset.cat, off: false });
        PR.toggleReadingFilter(anchor, '[data-cat="' + row.dataset.cat + '"]'); return;
      }
      const densityBtn = ev.target.closest("[data-density]");
      if (densityBtn) {
        PR.saveReadingFilter({ density: densityBtn.dataset.density });
        PR.toggleReadingFilter(anchor, '[data-density="' + densityBtn.dataset.density + '"]'); return;
      }
      const btn = ev.target.closest("[data-f]");
      if (!btn) return;
      if (["quality", "showSuppressed"].includes(btn.dataset.f)) {
        PR.saveReadingFilter({ [btn.dataset.f]: !f[btn.dataset.f] });
        PR.toggleReadingFilter(anchor, '[data-f="' + btn.dataset.f + '"]'); return;
      }
      closePop(true);
      if (btn.dataset.f === "enrich") { runEnrich(); return; }
      PR.saveReadingFilter({ only: "", off: btn.dataset.f === "off" && !f.off });
    };
  };
  async function runEnrich() {
    const pages = PR.readingTranslatedPages();
    if (!pages.length || enrichPending || PR.store.mode !== "server") return;
    enrichPending = true;
    try {
      await PR.api("/api/p/" + PR.pid + "/enrich", { method: "POST", body: { pages } });
      PR.toast("已排队精读全部 " + pages.length + " 页，完成后自动显示");
    } catch (err) { PR.toast("排队失败：" + err.message); }
    finally { enrichPending = false; }
  }
  function showHighlight(m) {
    const b = blocks().find((b) => b.id === m.dataset.block), data = dataFor(b);
    const hit = data.highlights.find((p) => p.range.start === Number(m.dataset.start) && p.range.end === Number(m.dataset.end) && p.h.category === m.dataset.cat);
    if (!hit) return;
    const annotations = data.annotations.filter((p) => p.range.start < hit.range.end && hit.range.start < p.range.end);
    const annHtml = annotations.map(({ a }) => '<div class="rf-ann" data-ann="' + PR.esc(a.id || "") + '"><span class="t">' +
      PR.esc(PR.READING_ANN_TYPES[a.type]) + '</span><span class="basis">' + PR.esc(PR.READING_ANN_BASES[a.basis]) + '</span>' +
      '<div class="q">「' + PR.esc(a.anchor_quote) + '」</div><div>' + PR.mdBlocks(a.content) + '</div><div class="reading-sources">' + PR.readingSourceHtml(a) + "</div></div>").join("");
    const cat = hit.h.category, only = PR.readingFilter.only;
    const score = importance(hit.h), reason = typeof hit.h.reason === "string" ? hit.h.reason.trim() : "";
    const metadata = '<div class="rf-score">' + (score === null ? "未评级" : "重要性 " + score + " / 5") + "</div>" +
      (reason ? '<div class="rf-reason"><b>标注理由</b><div>' + PR.esc(reason) + "</div></div>" : "");
    openPop(m, '<div class="hd"><i class="dot cat-' + cat + '"></i>' + PR.esc(CAT[cat]) + '</div><div class="rf-pop">' +
      PR.esc(hit.h.quote) + '</div>' + metadata + annHtml + '<div class="reading-sources">' + PR.readingSourceHtml(hit.h) +
      '</div><div class="rf-btns"><button type="button" class="btn sm line" data-f="only">' +
      (only === cat ? "取消只看" : "只看此类") + "</button></div>", CAT[cat] + "精读批注");
    PR.$("#popover").onclick = (ev) => {
      if (ev.target.closest("[data-reading-close]")) { closePop(true); return; }
      if (ev.target.closest('[data-f="only"]')) { closePop(true); PR.saveReadingFilter({ only: only === cat ? "" : cat, off: false }); PR.$('[data-act="reading"]')?.focus(); }
    };
  }
  document.addEventListener("click", (e) => {
    const source = e.target.closest && e.target.closest("[data-reading-source]");
    if (source) {
      const refs = PR.readingSources({ source_refs: [{ page: source.dataset.page, block_id: source.dataset.readingSource }] });
      if (!refs.length) return;
      e.preventDefault(); e.stopImmediatePropagation(); closePop(false);
      PR.openPage(refs[0].page, refs[0].block_id);
      PR.$("#pageview button")?.focus({ preventScroll: true }); return;
    }
    const m = e.target.closest && e.target.closest("mark.sem");
    if (!m || e.target.closest("a") || getSelection().toString()) return;
    e.stopImmediatePropagation(); showHighlight(m);
  }, true);
  document.addEventListener("keydown", (e) => {
    const p = readingPop(), m = e.target.closest && e.target.closest("mark.sem");
    if (p && e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closePop(true); return; }
    if (p && p.contains(e.target)) {
      e.stopPropagation();
      if (e.key === "Tab") {
        const buttons = PR.$$("button:not([disabled])", p), first = buttons[0], last = buttons[buttons.length - 1];
        if (e.shiftKey && e.target === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && e.target === last) { e.preventDefault(); first.focus(); }
      }
      return;
    }
    if (m && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); e.stopImmediatePropagation(); showHighlight(m); }
  }, true);

  function syncToolbar() {
    const btn = PR.$('[data-act="reading"]');
    if (!btn) return;
    const translated = PR.readingTranslatedPages();
    btn.style.display = (PR.store.mode === "server" && translated.length) || PR.hasReadingMarks() ? "" : "none";
    btn.title = translated.length && PR.store.mode === "server" ? "语义高亮筛选 · 手动精读全部 " + translated.length + " 页" : "语义高亮筛选";
  }
  PR.on("rendered", () => { PR.applyReadingMarks(); syncToolbar(); });
  PR.on("block-rendered", () => { PR.applyReadingMarks(); syncToolbar(); PR.renderMargin && PR.renderMargin(); PR.emit("reading-changed"); });
  PR.on("reader", (op) => {
    if (op && op.op === "edit") { PR.applyReadingMarks(); PR.renderMargin && PR.renderMargin(); PR.emit("reading-changed"); }
  });
})(window.PR);

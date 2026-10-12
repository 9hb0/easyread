+/* 语义高亮（六色精读标注）：翻译时模型顺带生成，存在 paper.json 各块的 highlights 上。
   这里把 quote 定位到渲染好的译文 DOM、包上带类别色的 mark，并提供类别筛选。
   类别颜色只在 base.css 的 --sem-* 变量和这里定义，别处不许再写死。 */
(function (PR) {
  "use strict";
  const S = PR.state;

  /* [id, 中文名]；颜色看 base.css 的 --sem-<id> */
  PR.READING_CATS = [
    ["background", "背景知识"],
    ["related_work", "相关工作"],
    ["problem", "解决的问题"],
    ["method", "提出的方法"],
    ["innovation", "核心创新"],
    ["evidence", "论据实验"],
  ];
  const CAT = Object.fromEntries(PR.READING_CATS);
  PR.readingCatName = (id) => CAT[id] || "";
  PR.READING_ANN_TYPES = {
    concept: "概念", method_explanation: "方法解释", design_rationale: "设计动机", formula: "公式",
    experiment: "实验", limitation: "局限", question: "疑问",
  };
  PR.READING_ANN_BASES = {
    author_statement: "作者陈述", source_supported: "有原文依据", ai_explanation: "AI 解释",
  };

  PR.readingFilter = PR.ls.get("pr-reading-filter", { off: false, only: "" });

  /* 一个块里所有 .zh（段落一个、列表每项一个）拼成一条文本流，occurrence 按块内顺序数 */
  function streams(host) {
    const parts = [];
    let full = "";
    for (const zh of PR.$$(".zh", host)) {
      const text = PR.textNodesIn(zh).map((t) => t.data).join("");
      parts.push({ zh, start: full.length, text });
      full += text;
    }
    return { parts, full };
  }

  function nth(text, q, n) {
    if (!q || n < 0) return -1;
    let i = text.indexOf(q);
    for (let k = 0; k < n && i >= 0; k++) i = text.indexOf(q, i + 1);
    return i;
  }

  function wrapOne(zh, start, end, h) {
    let pos = 0;
    for (const t of PR.textNodesIn(zh)) {
      const a = pos, b = pos + t.data.length;
      pos = b;
      if (b <= start || a >= end) continue;
      let node = t;
      const s = Math.max(start, a) - a, e = Math.min(end, b) - a;
      if (e < node.data.length) node.splitText(e);
      if (s > 0) node = node.splitText(s);
      const m = document.createElement("mark");
      m.className = "sem cat-" + h.category;
      m.dataset.cat = h.category;
      if (h.id) m.dataset.hl = h.id;
      m.title = CAT[h.category] || "";
      node.parentNode.insertBefore(m, node);
      m.appendChild(node);
    }
  }

  PR.hasReadingMarks = () => ((S.paper || {}).blocks || []).some((b) => b.highlights && b.highlights.length);

  PR.applyReadingMarks = function (onlyBlock) {
    const scope = onlyBlock ? document.getElementById("b-" + onlyBlock) : PR.$("#paper");
    if (!scope) return;
    PR.$$("mark.sem", scope).forEach((m) => m.replaceWith(...m.childNodes));
    scope.normalize();
    const f = PR.readingFilter;
    if (f.off || !S.paper) return;
    for (const b of S.paper.blocks || []) {
      if (!b.highlights || !b.highlights.length) continue;
      if (onlyBlock && b.id !== onlyBlock) continue;
      const host = document.getElementById("b-" + b.id);
      if (!host) continue;
      const { parts, full } = streams(host);
      if (!full) continue;
      for (const h of b.highlights) {
        if (!CAT[h.category]) continue;
        if (f.only && h.category !== f.only) continue;
        const i = nth(full, h.quote, h.occurrence || 0);
        if (i < 0) continue;
        for (const p of parts) {  // quote 不跨 .zh 单元，找到落在哪个里
          if (i >= p.start && i + h.quote.length <= p.start + p.text.length) {
            wrapOne(p.zh, i - p.start, i - p.start + h.quote.length, h);
            break;
          }
        }
      }
    }
  };

  /* ---------- 筛选 ---------- */
  function counts() {
    const c = {};
    for (const b of (S.paper || {}).blocks || []) for (const h of b.highlights || []) if (CAT[h.category]) c[h.category] = (c[h.category] || 0) + 1;
    return c;
  }

  PR.saveReadingFilter = function (f) {
    Object.assign(PR.readingFilter, f);
    PR.ls.set("pr-reading-filter", PR.readingFilter);
    PR.applyReadingMarks();
  };

  PR.toggleReadingFilter = function (anchor) {
    const c = counts();
    const rows = PR.READING_CATS.map(([id, zh]) =>
      '<button class="rf-row' + (PR.readingFilter.only === id ? " on" : "") + '" data-cat="' + id + '">' +
      '<i class="dot cat-' + id + '"></i><span>' + zh + '</span><b>' + (c[id] || 0) + "</b></button>").join("");
    const need = pagesNeeding();
    PR.popover(anchor, '<div class="hd">语义高亮</div><div class="rf-list">' + rows + "</div>" +
      '<div class="rf-btns"><button class="btn sm line" data-f="all">显示全部</button>' +
      '<button class="btn sm line" data-f="off">' + (PR.readingFilter.off ? "开启高亮" : "关闭高亮") + "</button>" +
      (PR.store.mode === "server" && need.length ? '<button class="btn sm line" data-f="enrich">补精读 ' + need.length + " 页</button>" : "") +
      "</div>", { sticky: true });
    PR.$("#popover").onclick = (ev) => {
      const row = ev.target.closest("[data-cat]");
      if (row) {
        const id = row.dataset.cat;
        PR.saveReadingFilter({ only: PR.readingFilter.only === id ? "" : id, off: false });
        PR.toggleReadingFilter(anchor);
        return;
      }
      const b = ev.target.closest("[data-f]");
      if (!b) return;
      if (b.dataset.f === "enrich") { PR.hidePopover(); runEnrich(); return; }
      if (b.dataset.f === "all") PR.saveReadingFilter({ only: "", off: false });
      else PR.saveReadingFilter({ only: "", off: !PR.readingFilter.off });
      PR.hidePopover();
    };
  };

  function pagesNeeding() {
    const p = S.paper || {};
    const done = (p.translation || {}).done_pages || [];
    const st = (p.reading || {}).pages || {};
    return done.filter((n) => {
      const s = (st[n] || {}).status;
      return !s || ["failed", "stale", "pending"].includes(s);
    });
  }

  async function runEnrich() {
    try {
      await PR.api("/api/p/" + PR.pid + "/enrich", { method: "POST", body: {} });
      PR.toast("精读补处理已排队，完成后自动显示");
    } catch (err) {
      PR.toast("排队失败：" + PR.esc(err.message));
    }
  }

  /* ---------- 点高亮：看类别 ---------- */
  document.addEventListener("click", (e) => {
    const m = e.target.closest && e.target.closest("mark.sem");
    if (!m || getSelection().toString()) return;
    e.stopPropagation();
    const name = CAT[m.dataset.cat] || "";
    const only = PR.readingFilter.only;
    const blk = m.closest(".blk");
    const b = blk && PR.blockById[blk.dataset.id];
    const anns = (b && b.annotations) || [];
    const annHtml = anns.slice(0, 2).map((a) =>
      '<div class="rf-ann"><span class="t">' + PR.esc((PR.READING_ANN_TYPES || {})[a.type] || a.type || "批注") + "</span>" +
      (a.anchor_quote ? '<span class="q">「' + PR.esc(a.anchor_quote) + "」</span>" : "") +
      "<div>" + PR.mdBlocks(a.content) + "</div></div>").join("") +
      (anns.length > 2 ? '<div class="rf-ann-more">…共 ' + anns.length + " 条批注，见页边卡片</div>" : "");
    PR.popover(m, '<div class="hd"><i class="dot cat-' + m.dataset.cat + '"></i>' + PR.esc(name) + "</div>" +
      '<div class="rf-pop">AI 精读把这句标为「' + PR.esc(name) + '」。</div>' + annHtml +
      '<div class="rf-btns"><button class="btn sm line" data-f="only">' + (only === m.dataset.cat ? "取消只看" : "只看此类") + "</button></div>", { sticky: true });
    PR.$("#popover").onclick = (ev) => {
      const b = ev.target.closest("[data-f]");
      if (!b) return;
      PR.hidePopover();
      PR.saveReadingFilter({ only: only === m.dataset.cat ? "" : m.dataset.cat, off: false });
    };
  }, true);

  /* ---------- 渲染时机 ---------- */
  function syncToolbar() {
    const btn = document.querySelector('[data-act="reading"]');
    if (btn) btn.style.display = PR.hasReadingMarks() ? "" : "none";
  }
  PR.on("rendered", () => { PR.applyReadingMarks(); syncToolbar(); });
  PR.on("block-rendered", (id) => PR.applyReadingMarks(id));
})(window.PR);

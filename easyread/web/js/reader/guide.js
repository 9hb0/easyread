/* 研究导航：只展示有当前原文来源的研究要点，覆盖范围按页面绑定重新计算。 */
(function (PR) {
  "use strict";
  const S = PR.state;
  const GROUPS = [
    ["background", "关键背景"], ["related_work", "相关工作"], ["problem", "研究问题"],
    ["method", "提出的方法"], ["innovation", "核心创新"], ["evidence", "主要证据"],
    ["finding", "关键结论"], ["limitation", "局限性"],
  ];
  const STATUS = { provisional: "待验证", supported: "有原文依据", unsupported: "证据不足" };
  const array = (v) => Array.isArray(v) ? v : [];
  const numbers = (v) => [...new Set(array(v).map(Number).filter((n) => Number.isInteger(n) && n > 0))].sort((a, b) => a - b);
  const panel = () => PR.$("#guidepanel");
  let returnFocus = null;

  function pageList(pages) {
    const runs = [];
    for (let i = 0; i < pages.length; i++) {
      const first = pages[i];
      while (i + 1 < pages.length && pages[i + 1] === pages[i] + 1) i++;
      runs.push(first === pages[i] ? String(first) : first + "–" + pages[i]);
    }
    return runs.length ? "第 " + runs.join("、") + " 页" : "暂无";
  }
  function itemHtml(i, refs, relation) {
    const target = refs[0], status = Object.prototype.hasOwnProperty.call(STATUS, i.status) ? i.status : "provisional";
    const st = status === "supported" ? " ok" : status === "unsupported" ? " bad" : "";
    return '<div class="gd-entry"><button type="button" class="gd-item' + (relation ? " rel" : "") + '" data-goto="' +
      PR.esc(target.block_id) + '" data-page="' + target.page + '" title="定位第 ' + target.page + ' 页译文">' +
      '<span class="gd-st' + st + '">' + STATUS[status] + '</span><span>' + PR.md(i.content, { cite: false, xref: false }) +
      '</span><small>p.' + target.page + (refs.length > 1 ? " +" + (refs.length - 1) : "") + '</small></button>' +
      '<div class="reading-sources">' + PR.readingSourceHtml({ source_refs: refs }) + "</div></div>";
  }
  function render() {
    const el = panel();
    if (!el || !PR.readingPageValid) return;
    const paper = S.paper || {}, reading = paper.reading || {}, g = paper.reading_guide || {}, tr = paper.translation || {}, meta = paper.meta || {};
    const sourcePages = numbers(array(meta.pages).map((p) => p.n));
    const total = Number(meta.page_count) || sourcePages.length;
    const all = sourcePages.length === total ? sourcePages : total > 0 ? Array.from({ length: total }, (_, i) => i + 1) : [];
    const done = PR.readingTranslatedPages(), analyzed = done.filter(PR.readingPageValid);
    const targets = numbers(tr.target_pages).length ? numbers(tr.target_pages) : done;
    const full = all.length > 0 && all.every((n) => analyzed.includes(n));
    const rangeDone = targets.length > 0 && targets.every((n) => analyzed.includes(n));
    const state = full ? '<span class="gd-flag done">全文分析</span>' : rangeDone ? '<span class="gd-flag done">范围分析</span>' :
      '<span class="gd-flag">' + (analyzed.length ? "初步分析" : "等待精读") + "</span>";
    const progress = "已分析 " + analyzed.length + " / " + done.length + " 页译文" + (total ? " · 全文 " + total + " 页" : "");
    const types = new Set(GROUPS.map(([t]) => t));
    const items = reading.version === PR.READING_VERSION ? array(reading.insights).map((i) => ({ i, refs: PR.readingSources(i) }))
      .filter(({ i, refs }) => i && typeof i.content === "string" && i.content.trim() && types.has(i.type) && refs.length) : [];
    let body = '<div class="gd-range">已分析：' + pageList(analyzed) + '<br>已译：' + pageList(done) +
      '<br>' + (tr.requested_scope === "body" ? "所选正文" : "所选范围") + "：" + pageList(targets) + "</div>";
    const synthesisValid = g.version === PR.READING_VERSION && reading.version === PR.READING_VERSION;
    const summary = typeof g.summary === "object" && g.summary ? g.summary.content : g.summary;
    const summaryRefs = PR.readingSources({ source_refs: (g.summary && g.summary.source_refs) || g.summary_refs || g.summary_source_refs });
    if (synthesisValid && typeof summary === "string" && summary.trim() && summaryRefs.length) body +=
      '<div class="gd-summary">' + PR.md(summary, { cite: false, xref: false }) + '<div class="reading-sources">' + PR.readingSourceHtml({ source_refs: summaryRefs }) + "</div></div>";
    for (const [type, label] of GROUPS) {
      const rows = items.filter(({ i }) => i.type === type);
      if (rows.length) body += '<div class="gd-group"><div class="gd-gt">' + label + "<b>" + rows.length + "</b></div>" +
        rows.map(({ i, refs }) => itemHtml(i, refs, false)).join("") + "</div>";
    }
    const ids = new Set(items.map(({ i }) => i.id));
    const relations = synthesisValid ? array(g.relations).map((r) => ({ r, refs: PR.readingSources(r) })).filter(({ r, refs }) =>
      r && ids.has(r.from) && ids.has(r.to) && r.from !== r.to && typeof r.content === "string" && r.content.trim() && refs.length) : [];
    if (relations.length) body += '<div class="gd-group"><div class="gd-gt">逻辑关系</div>' +
      relations.map(({ r, refs }) => itemHtml(r, refs, true)).join("") + "</div>";
    if (!items.length && !summaryRefs.length) body += '<div class="gd-empty">还没有有效的研究要点。<br>请从顶栏“标注”手动启动精读。</div>';
    // 保留 pageview.js 已绑定拖动/键盘事件的面板握柄。
    let content = PR.$(".gd-content", el);
    if (!content) { content = PR.el("div", { class: "gd-content" }); el.appendChild(content); }
    const active = el.contains(document.activeElement) ? document.activeElement.dataset.goto : null;
    content.innerHTML = '<div class="gd-head"><div><b>研究导航</b><small>' + PR.esc(progress) + '</small></div><span class="grow"></span>' +
      state + '<button type="button" class="btn icon" data-gd="close" aria-label="关闭研究导航" title="关闭">×</button></div>' +
      '<div class="gd-scroll">' + body + "</div>";
    if (active) (PR.$$("[data-goto]", content).find((b) => b.dataset.goto === active) || PR.$("[data-gd=close]", content))?.focus({ preventScroll: true });
  }
  PR.guideOpen = () => PR.side === "guide";
  PR.toggleGuide = function (force) {
    const open = force != null ? force : !PR.guideOpen();
    if (open) returnFocus = document.activeElement;
    PR.openSide(open ? "guide" : null);
    if (open) { render(); PR.$("[data-gd=close]", panel())?.focus({ preventScroll: true }); }
    else (returnFocus && returnFocus.isConnected && returnFocus !== document.body ? returnFocus : PR.$('[data-act="guide"]'))?.focus({ preventScroll: true });
  };
  document.addEventListener("click", (e) => {
    const el = panel();
    if (!el || !el.contains(e.target)) return;
    if (e.target.closest("[data-gd=close]")) { PR.toggleGuide(false); return; }
    const b = e.target.closest(".gd-item[data-goto]");
    if (!b) return;
    const refs = PR.readingSources({ source_refs: [{ page: b.dataset.page, block_id: b.dataset.goto }] });
    if (!refs.length) return;
    const host = document.getElementById("b-" + refs[0].block_id);
    if (!host) return;
    if (innerWidth <= 760) PR.openSide(null);
    PR.jumpTo(host.id); host.setAttribute("tabindex", "-1"); host.focus({ preventScroll: true });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && PR.guideOpen() && !PR.$("#popover.open")) {
      e.preventDefault(); e.stopImmediatePropagation(); PR.toggleGuide(false);
    }
  }, true);
  PR.on("rendered", () => { if (PR.guideOpen()) render(); });
  PR.on("reading-changed", () => { if (PR.guideOpen()) render(); });
})(window.PR);

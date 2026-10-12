/* 研究导航侧栏：汇总翻译时逐批提取的研究要点（问题/方法/创新/证据/局限）。
   数据在 paper.json 的 reading.insights 和 reading_guide；点条目跳回对应原文。 */
(function (PR) {
  "use strict";
  const S = PR.state;

  const GROUPS = [
    ["problems", "研究问题", "problem"],
    ["methods", "提出的方法", "method"],
    ["innovations", "核心创新", "innovation"],
    ["evidence", "主要证据", "evidence"],
    ["limitations", "重要局限", "limitation"],
  ];
  const STATUS = { provisional: "待验证", supported: "有依据", unsupported: "证据不足" };

  const panel = () => PR.$("#guidepanel");
  const insights = () => ((S.paper || {}).reading || {}).insights || [];
  const guide = () => (S.paper || {}).reading_guide || {};

  function firstBlock(i) {
    const r = (i.source_refs || [{ page: i.page, block: i.block }])[0] || {};
    return r.block || i.block || "";
  }

  function itemHtml(i) {
    const refs = (i.source_refs || [{ page: i.page, block: i.block }]);
    const target = refs[0] || {};
    const st = i.status === "supported" ? " ok" : (i.status === "unsupported" ? " bad" : "");
    return '<button class="gd-item" data-goto="' + PR.esc(firstBlock(i)) + '" title="定位到第 ' + (target.page || "?") + ' 页">' +
      '<span class="gd-st' + st + '">' + PR.esc(STATUS[i.status] || "待验证") + "</span>" +
      "<span>" + PR.md(i.content, { cite: false, xref: false }) + "</span>" +
      "<small>p." + (target.page || "?") + (refs.length > 1 ? " +" + (refs.length - 1) : "") + "</small></button>";
  }

  function render() {
    const el = panel();
    if (!el) return;
    const g = guide();
    const cov = g.coverage || {};
    const items = insights();
    const cov0 = g.coverage || {};
    const full = g.status === "complete" && (cov0.translated || []).length >= (cov0.total || 0);
    const state = g.status === "complete" ? '<span class="gd-flag done">' + (full ? "全文分析" : "范围分析") + "</span>"
      : items.length ? '<span class="gd-flag">初步分析</span>' : "";
    const progress = (cov.analyzed || []).length ? "已分析 " + cov.analyzed.length + " / " + (cov.translated || []).length + " 页译文" : "还没有分析数据";
    let body = "";
    if (g.summary) body += '<div class="gd-summary">' + PR.md(g.summary) + "</div>";
    for (const [key, label, typ] of GROUPS) {
      const rows = items.filter((i) => i.type === typ);
      if (!rows.length) continue;
      body += '<div class="gd-group"><div class="gd-gt">' + label + "<b>" + rows.length + "</b></div>" + rows.map(itemHtml).join("") + "</div>";
    }
    const rels = g.relations || [];
    if (rels.length) {
      const map = {};
      for (const i of items) map[i.id] = firstBlock(i);
      body += '<div class="gd-group"><div class="gd-gt">逻辑关系</div>' + rels.map((r) =>
        '<button class="gd-item rel" data-goto="' + PR.esc(map[r.from] || "") + '"><span class="gd-rel">' +
        PR.md(r.content, { cite: false, xref: false }) + "</span></button>").join("") + "</div>";
    }
    if (!body) body = '<div class="gd-empty">还没有研究要点。<br>翻译完成后自动生成；<br>老论文用顶栏“标注”里的“补精读”。</div>';
    el.innerHTML = '<div class="gd-head"><div><b>研究导航</b><small>' + PR.esc(progress) + "</small></div>" +
      '<span class="grow"></span>' + state + '<button class="btn icon" data-gd="close" title="关闭">×</button></div>' +
      '<div class="gd-scroll">' + body + "</div>";
  }

  PR.guideOpen = () => PR.side === "guide";
  PR.toggleGuide = function (force) {
    const open = force != null ? force : PR.side !== "guide";
    PR.openSide(open ? "guide" : null);
    if (open) render();
  };

  /* 点条目：跳回原文出处 */
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest(".gd-item[data-goto]");
    if (b && b.dataset.goto) PR.jumpTo("b-" + b.dataset.goto);
    const c = e.target.closest && e.target.closest("[data-gd=close]");
    if (c) PR.toggleGuide(false);
  });

  /* 面板开着时数据更新了就重画（翻译批次提交后会触发） */
  PR.on("rendered", () => { if (PR.side === "guide") render(); });
})(window.PR);

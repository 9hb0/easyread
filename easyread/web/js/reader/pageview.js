/* 右侧面板：原文页（随阅读位置翻页、框出当前段）。和笔记面板共用右侧，一次开一个。 */
(function (PR) {
  "use strict";
  const S = PR.state;
  const body = document.body;
  let pvPage = 1, pvBlock = null;
  const pages = () => (S.paper.meta || {}).pages || [];

  /* 右侧面板宽度：桌面端可拖动，离线版和服务版都记在当前浏览器。 */
  const SIDE_KEY = "easyread-reader-side-w";
  const root = document.documentElement;
  const sidePanels = () => PR.$$(".side-panel");
  function clampSideWidth(w) {
    const max = Math.max(420, Math.min(760, innerWidth - 320));
    return Math.max(360, Math.min(max, Number(w) || Math.min(innerWidth * .44, 720)));
  }
  function setSideWidth(w, save) {
    const width = clampSideWidth(w);
    root.style.setProperty("--reader-side-w", width + "px");
    if (save) PR.ls.set(SIDE_KEY, Math.round(width));
    return width;
  }
  function initSideWidth() {
    const saved = PR.ls.get(SIDE_KEY, null);
    setSideWidth(saved || Math.min(innerWidth * .44, 720), false);
  }
  initSideWidth();
  sidePanels().forEach((panel) => {
    const grip = PR.$("[data-side-grip]", panel);
    if (!grip) return;
    grip.addEventListener("pointerdown", (e) => {
      if (innerWidth <= 760) return;
      e.preventDefault();
      const startX = e.clientX, startW = panel.getBoundingClientRect().width;
      document.body.classList.add("reader-resizing");
      grip.setPointerCapture?.(e.pointerId);
      const move = (ev) => setSideWidth(startW + startX - ev.clientX, false);
      const stop = () => {
        grip.releasePointerCapture?.(e.pointerId);
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", stop);
        grip.removeEventListener("pointercancel", stop);
        document.body.classList.remove("reader-resizing");
        setSideWidth(parseFloat(getComputedStyle(panel).width), true);
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", stop);
      grip.addEventListener("pointercancel", stop);
    });
    grip.addEventListener("keydown", (e) => {
      const current = panel.getBoundingClientRect().width;
      if (e.key === "ArrowLeft") { e.preventDefault(); setSideWidth(current + 16, true); }
      if (e.key === "ArrowRight") { e.preventDefault(); setSideWidth(current - 16, true); }
      if (e.key === "Home") { e.preventDefault(); setSideWidth(Math.min(innerWidth * .44, 720), true); }
    });
  });
  window.addEventListener("resize", () => setSideWidth(parseFloat(getComputedStyle(root).getPropertyValue("--reader-side-w")), false));

  /* 右侧面板开关：pages | notes | chat | deepread | null */
  /* 面板先滑出来（只动面板，不卡），滑完再让正文让位、重排一次。
     长论文有几万个节点，正文宽度一变就要整页重排（两三百毫秒），放在点击的当下会让人觉得按钮反应慢。 */
  PR.side = null;
  let sideT = null;
  PR.openSide = function (name) {
    PR.side = name;
    body.classList.toggle("pv-open", name === "pages");
    body.classList.toggle("np-open", name === "notes");
    body.classList.toggle("ch-open", name === "chat");
    body.classList.toggle("dr-open", name === "deepread");
    PR.$('[data-act="chat"]').classList.toggle("on", name === "chat");
    PR.$('[data-act="deepread"]').classList.toggle("on", name === "deepread");
    PR.$('[data-act="pages"]').classList.toggle("on", name === "pages");
    PR.$('[data-act="notes"]').classList.toggle("on", name === "notes");
    clearTimeout(sideT);
    if (name) { const t = PR.$("#toast"); if (t) t.classList.remove("open"); }  // 提示条别挡住面板底部的输入框
    if (body.classList.contains("side-open") === !!name) return;  // 面板之间切换：正文宽度不变
    sideT = setTimeout(() => requestAnimationFrame(() => {
      const anchor = PR.readingBlock && PR.readingBlock();
      const node = anchor && document.getElementById("b-" + anchor);
      const before = node ? node.getBoundingClientRect().top : 0;
      body.classList.toggle("side-open", !!PR.side);
      PR.fitWide(); PR.renderMargin();
      if (node) window.scrollBy(0, node.getBoundingClientRect().top - before);  // 重排后还停在刚才读的地方
    }), 300);
  };

  PR.togglePages = function (force) {
    const open = force != null ? force : PR.side !== "pages";
    PR.openSide(open ? "pages" : null);
    if (open) PR.syncPage(true);
  };
  PR.openPage = function (page, blockId) {
    pvBlock = blockId || null;
    if (PR.side !== "pages") PR.openSide("pages");
    showPage(page, blockId);
  };

  /* 原图是 2.4 倍渲染（约 1500 像素宽、几百 KB），面板用不了那么大：要一张和面板一样宽的，服务端生成一次后缓存 */
  function srcOf(n) {
    const p = pages()[n - 1];
    if (!p) return "";
    const base = PR.imageUrl(p.img);
    if (PR.store.mode !== "server") return base;
    const need = (PR.$(".pv-scroll").clientWidth || 480) * (body.classList.contains("pv-zoom") ? 1.65 : 1) * (devicePixelRatio || 1);
    return need <= 1000 ? base + "?w=1000" : need <= 1600 ? base + "?w=1600" : base;  // 1000 宽的服务端已提前生成好
  }
  const preloaded = new Set();
  function preload(n) {
    const s = srcOf(n);
    if (s && !preloaded.has(s)) { preloaded.add(s); const im = new Image(); im.decoding = "async"; im.src = s; }
  }
  PR.preloadPage = () => { const b = PR.blockById[PR.readingBlock()]; if (b && b.page) preload(b.page); };

  function showPage(page, blockId) {
    const list = pages();
    if (!list.length) return;
    pvPage = Math.min(list.length, Math.max(1, page));
    const img = PR.$(".pv-page img");
    img.decoding = "async";
    const src = srcOf(pvPage);
    if (img.getAttribute("src") !== src) { img.setAttribute("src", src); PR.$(".pv-page").classList.add("loading"); img.onload = () => PR.$(".pv-page").classList.remove("loading"); }
    preload(pvPage + 1); preload(pvPage - 1);
    PR.$(".pv-label").textContent = "第 " + pvPage + " / " + list.length + " 页";
    const pdf = PR.$('[data-pv="pdf"]');
    const url = PR.pdfUrl(pvPage);
    pdf.style.display = url ? "" : "none";
    if (url) pdf.href = url;
    const hl = PR.$(".pv-hl");
    const loc = blockId && S.layout[blockId];
    if (loc && loc.page === pvPage) {
      const [x0, y0, x1, y1] = loc.box;
      Object.assign(hl.style, { left: (x0 * 100 - 0.8) + "%", top: (y0 * 100 - 0.4) + "%", width: ((x1 - x0) * 100 + 1.6) + "%", height: ((y1 - y0) * 100 + 0.8) + "%" });
      hl.classList.add("on");
      const scroller = PR.$(".pv-scroll");
      const doScroll = () => { const h = PR.$(".pv-page").offsetHeight;  // 原页里框出的那段也放在面板中间
        scroller.scrollTo({ top: Math.max(0, ((y0 + y1) / 2) * h + 18 - scroller.clientHeight / 2), behavior: "smooth" }); };
      img.complete ? doScroll() : img.addEventListener("load", doScroll, { once: true });
    } else hl.classList.remove("on");
  }

  PR.syncPage = function (force) {
    if (PR.side !== "pages") return;
    if (!force && !PR.$(".pv-follow input").checked) return;
    const id = (PR.currentBlock && PR.currentBlock()) || PR.readingBlock();
    const b = PR.blockById[id];
    if (!b) return;
    if (!force && id === pvBlock) return;
    pvBlock = id;
    const loc = S.layout[id];
    showPage(loc ? loc.page : b.page, id);
  };

  PR.$("#pageview").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pv]");
    if (!b) return;
    const act = b.dataset.pv;
    if (act === "close") PR.togglePages(false);
    if (act === "prev") showPage(pvPage - 1, pvBlock);
    if (act === "next") showPage(pvPage + 1, pvBlock);
    if (act === "zoom") { body.classList.toggle("pv-zoom"); b.textContent = body.classList.contains("pv-zoom") ? "适宽" : "放大"; showPage(pvPage, pvBlock); }
  });
  PR.pageStep = (d) => showPage(pvPage + d, pvBlock);
})(window.PR);

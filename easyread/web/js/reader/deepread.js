/* 论文精读侧栏：只在用户打开并点击生成后请求模型。 */
(function (PR) {
  "use strict";
  const S = PR.state;
  let state = null;
  let wiki = null;
  let pollT = null;

  const panel = () => PR.$("#deepreadpanel");
  const active = () => state && ["queued", "running"].includes(state.state);

  function defaultState() {
    return { state: "idle", message: "还没有生成精读", content: "", has_document: false, chars: 0, error: "" };
  }

  function defaultWiki() {
    return { state: "idle", message: "", log: "", vault: "", run_dir: "" };
  }

  /* 知识库入库（paper-ingest）的状态条：running 显示进度和取消，结束后保留结果，直到下次入库 */
  function wikiHtml() {
    const w = wiki;
    if (!w || w.state === "idle") return "";
    if (w.state === "running")
      return '<div class="dr-wiki"><span class="spin"></span><span class="grow">' + PR.esc(w.message || "知识库入库中") + '，可能要几分钟</span>' +
        '<button class="btn sm line" data-dr="wikilog">日志</button><button class="btn sm line" data-dr="wikicancel">取消</button></div>';
    const cls = w.state === "done" ? " ok" : (w.state === "error" ? " bad" : "");
    return '<div class="dr-wiki' + cls + '"><span class="grow">' + PR.esc(w.message || "入库结束") + '</span>' +
      '<button class="btn sm line" data-dr="wikilog">' + (w.state === "error" ? "详情" : "日志") + "</button></div>";
  }

  function render() {
    const el = panel();
    if (!el) return;
    const s = state || defaultState();
    const busy = active();
    let content = "";
    if (busy) content += '<div class="dr-status"><span class="spin"></span>' + PR.esc(s.message || "模型思考中") + "</div>";
    else if (s.error) content += '<div class="dr-error">' + PR.esc(s.error) + "</div>";
    if (s.content) content += '<div class="dr-doc">' + PR.mdBlocks(s.content) + "</div>";
    else if (!busy && !s.error) content += '<div class="dr-empty"><b>还没有精读文档</b><p>点击下面的按钮后，模型才会开始阅读这篇论文。</p></div>';
    const buttons = busy ? "" :
      '<button class="btn sm accent" data-dr="generate">' + (s.content ? "重新生成" : "生成精读") + "</button>";
    const tools = s.content && !busy ?
      '<button class="btn sm line" data-dr="copy">复制</button><button class="btn sm line" data-dr="download">下载 Markdown</button>' +
      (PR.store.mode === "server" ? '<button class="btn sm line" data-dr="wiki">入知识库</button>' : "") : "";
    el.innerHTML = '<div class="dr-head"><div><b>精读论文</b><small>' + (s.chars ? s.chars + " 字" : "最多 5000 字") + '</small></div><span class="grow"></span><button class="btn icon" data-dr="settings" title="精读设置">⚙</button><button class="btn icon" data-dr="close" title="关闭">×</button></div>' +
      '<div class="dr-actions">' + buttons + tools + '<span class="grow"></span>' + (s.updated ? '<span class="hint">' + PR.esc(PR.shortTime(s.updated)) + "</span>" : "") + "</div>" +
      wikiHtml() +
      '<div class="dr-scroll">' + content + "</div>";
  }

  async function load() {
    if (PR.store.mode !== "server") return;
    try {
      const [dr, wk] = await Promise.all([
        PR.api("/api/p/" + PR.pid + "/deepread"),
        PR.api("/api/p/" + PR.pid + "/wikiingest").catch(() => null),
      ]);
      state = dr;
      wiki = wk || defaultWiki();
      render();
    }
    catch (e) { state = Object.assign(defaultState(), { error: e.message }); render(); }
  }

  function startPolling() {
    clearInterval(pollT);
    pollT = setInterval(() => { if (PR.side === "deepread") load(); }, 1800);
  }

  PR.deepReadOpen = () => PR.side === "deepread";
  PR.toggleDeepRead = function (force) {
    const open = force != null ? force : PR.side !== "deepread";
    PR.openSide(open ? "deepread" : null);
    if (open) { render(); load(); startPolling(); }
    else { clearInterval(pollT); pollT = null; }
  };

  function download() {
    const meta = S.paper.meta || {};
    const stem = (meta.short_zh || meta.title_zh || meta.title_en || "论文").replace(/[\\/:*?"<>|]/g, "");
    const a = PR.el("a", { href: URL.createObjectURL(new Blob([state.content], { type: "text/markdown;charset=utf-8" })), download: stem + "-精读.md" });
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  async function ingest() {
    if (!state || !state.content) return PR.toast("还没有精读文档，先生成精读");
    let vault = (wiki || {}).vault || "";
    if (!vault) {
      try { vault = (((await PR.api("/api/config")).config || {}).wiki || {}).vault || ""; } catch (e) { /* 启动失败时下面统一报 */ }
    }
    if (!vault) {
      PR.toast("先在设置的“精读论文”里填论文入库知识库路径");
      return PR.openSettings("deepread");
    }
    const ok = await PR.confirm({
      title: "放进知识库？",
      body: "会把这篇的精读文档和原论文 PDF 交给知识库（" + vault + "）的入库流程：入库模型整理候选、做完整性筛查，通过后发布并更新索引。通常要几分钟，中途可取消。",
      ok: "开始入库",
    });
    if (!ok) return;
    try {
      wiki = await PR.api("/api/p/" + PR.pid + "/wikiingest", { method: "POST", body: {} });
      PR.toast("入库已开始");
      render();
      startPolling();
    } catch (err) { PR.toast("没能开始入库：" + PR.esc(err.message)); }
  }

  PR.$("#deepreadpanel").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-dr]");
    if (!b) return;
    const act = b.dataset.dr;
    if (act === "close") return PR.toggleDeepRead(false);
    if (act === "settings") return PR.openSettings("deepread");
    if (act === "copy") { try { await navigator.clipboard.writeText(state.content); PR.toast("已复制精读文档"); } catch (_) { PR.toast("复制失败，请手动选中文本"); } return; }
    if (act === "download") return download();
    if (act === "wiki") return ingest();
    if (act === "wikicancel") {
      try {
        const r = await PR.api("/api/p/" + PR.pid + "/wikiingest/cancel", { method: "POST", body: {} });
        PR.toast(r.ok ? "已取消入库" : (r.message || "没能取消"));
      } catch (err) { PR.toast(PR.esc(err.message)); }
      return load();
    }
    if (act === "wikilog") {
      const w = wiki || defaultWiki();
      return PR.showText("知识库入库日志", (w.log || "（还没有记录）") + (w.run_dir ? "\n\n运行目录：" + w.run_dir : ""));
    }
    if (act === "generate") {
      b.disabled = true;
      try { state = await PR.api("/api/p/" + PR.pid + "/deepread", { method: "POST", body: {} }); PR.toast("精读已排队"); render(); startPolling(); }
      catch (err) { state = Object.assign(defaultState(), { error: err.message }); render(); }
    }
  });
})(window.PR);

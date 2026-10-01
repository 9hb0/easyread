/* 设置对话框（文献库页和阅读页共用）：外壳、分页、保存。
   四页：翻译（本文件）、问 AI / 阅读 / 快捷键（settings-tabs.js）。
   PR.openSettings("chat") 直接打开某一页；不指定就从“翻译”页开始（不记上次停在哪页）。 */
(function (PR) {
  "use strict";
  const dlg = () => PR.$("#settingsDlg");
  const ALL_TABS = [["engine", "翻译"], ["chat", "问 AI"], ["deepread", "精读论文"], ["reading", "阅读"], ["library", "侧边栏"], ["keys", "快捷键"]];
  const tabs = () => ALL_TABS.filter(([k]) => PR.settingsTabs[k]);  // “侧边栏”页只在文献库页面有
  PR.settingsTabs = PR.settingsTabs || {};
  const st = (PR.settingsState = { tab: "engine", cfg: null, presets: [], groups: [], found: null, chat: null, ui: null });

  PR.opt = (list, val) => list.map(([v, l]) => '<option value="' + PR.esc(v) + '"' + (String(v) === String(val) ? " selected" : "") + ">" + PR.esc(l) + "</option>").join("");

  PR.openSettings = async function (tab) {
    const [d, chat] = await Promise.all([PR.api("/api/config"), PR.api("/api/chat/models").catch(() => null)]);
    Object.assign(st, { tab: typeof tab === "string" ? tab : "engine", cfg: d.config, presets: d.presets, groups: d.groups || [], chat,
      apiKind: null, ui: { features: Object.assign({}, PR.features), keys_on: PR.keysOn, keys: Object.assign({}, PR.keymap) },
      theme: PR.ls.get("easyread-prefs", {}).theme || "auto", recording: null, editing: null, form: null, chatKeys: null, apiModels: [] });
    render();
    dlg().classList.add("open");
    // 每次打开都问一次（后端有缓存，很快）：刚装好或更新了 Claude Code / Codex，版本号和模型名单马上跟上
    const r = await PR.api("/api/engines").catch(() => null);
    if (r && (JSON.stringify([r.found, r.models]) !== JSON.stringify([st.found, st.models]))) {
      st.found = r.found; st.models = r.models;
      if (dlg().classList.contains("open")) { sync(); render(); }
    }
  };
  const btn = PR.$("#settingsBtn");
  if (btn) btn.onclick = () => PR.openSettings();

  function sync() { const t = PR.settingsTabs[st.tab]; if (t && t.sync) t.sync(st, dlg()); }
  PR.settingsRender = render;
  function render() {
    const t = PR.settingsTabs[st.tab];
    dlg().querySelector(".dialog").innerHTML =
      '<div class="set-head"><h2>设置</h2><div class="set-tabs">' + tabs().map(([k, l]) => '<button data-set-tab="' + k + '" class="' + (st.tab === k ? "on" : "") + '">' + l + "</button>").join("") + "</div></div>" +
      '<div class="set-body">' + (t ? t.render(st) : "") + "</div>" +
      '<div class="actions set-foot"><button class="linkish" id="showLog">运行日志</button><span class="grow"></span><button class="btn" id="setCancel">取消</button><button class="btn primary" id="setSave">保存</button></div>';
  }

  async function save() {
    sync();
    const r = await PR.api("/api/config", { method: "POST", body: PR.settingsTabs.engine.collect(st) });
    st.cfg = r.config;
    if (st.chat) st.chat = await PR.api("/api/chat/models", { method: "POST", body: { models: st.chat.models, default: st.chat.default, keys: st.chatKeys || {} } });
    PR.applyUi(st.ui, true);
    PR.ls.set("easyread-auto-translate", !!st.cfg.auto_translate);
    const prefs = PR.ls.get("easyread-prefs", {});
    if (prefs.theme !== st.theme) { prefs.theme = st.theme; PR.ls.set("easyread-prefs", prefs); PR.savePrefs("reader", { theme: st.theme }); if (PR.prefs) PR.prefs.theme = st.theme; }
    PR.applyTheme(st.theme);
    dlg().classList.remove("open");
    PR.toast("设置已保存");
    PR.onSettingsSaved && PR.onSettingsSaved();
    PR.emit("settings-saved", st);
  }

  PR.showText = function (title, text) {
    const d = PR.$("#textDlg");
    d.querySelector(".dialog").innerHTML = "<h2>" + PR.esc(title) + '</h2><pre class="logview">' + PR.esc(text || "（还没有记录）") + '</pre><div class="actions"><button class="btn" data-close>关闭</button></div>';
    d.classList.add("open");
    const pre = d.querySelector("pre"); pre.scrollTop = pre.scrollHeight;
  };
  const td = PR.$("#textDlg");
  if (td) td.addEventListener("click", (e) => { if (e.target.id === "textDlg" || e.target.closest("[data-close]")) td.classList.remove("open"); });

  dlg().addEventListener("click", async (e) => {
    if (e.target === dlg() || e.target.closest("#setCancel")) { st.recording = null; return dlg().classList.remove("open"); }
    const tb = e.target.closest("[data-set-tab]");
    if (tb) { sync(); st.tab = tb.dataset.setTab; st.recording = null; st.editing = null; render(); return; }
    if (e.target.closest("#showLog")) { const r = await PR.api("/api/log"); PR.showText("运行日志", r.text + "\n\n（完整日志：" + r.path + "）"); return; }
    if (e.target.closest("#setSave")) { try { await save(); } catch (err) { PR.toast("保存失败：" + PR.esc(err.message)); } return; }
    const t = PR.settingsTabs[st.tab];
    if (t && t.click && (await t.click(e, st, dlg()))) render();
  });
  dlg().addEventListener("change", (e) => {
    const t = PR.settingsTabs[st.tab];
    if (t && t.change && t.change(e, st, dlg())) render();
  });
  document.addEventListener("keydown", (e) => {
    if (!dlg().classList.contains("open")) return;
    const t = PR.settingsTabs[st.tab];
    if (st.recording && t && t.key) { e.preventDefault(); e.stopImmediatePropagation(); if (t.key(e, st)) render(); return; }
    if (e.key === "Escape") dlg().classList.remove("open");
  }, true);

  /* ---------- 翻译 ---------- */
  function badge(name) {
    if (!st.found) return '<span class="badge"><span class="spin"></span>检测中</span>';
    const f = st.found[name] || {};
    return f.found ? '<span class="badge ok">已安装' + (f.version ? " " + PR.esc((f.version.match(/\d+(\.\d+)+/) || [""])[0]) : "") + "</span>" : '<span class="badge">本机没找到</span>';
  }
  function cliFields(name) {
    const c = st.cfg[name];
    const model = PR.cliModelInput(st, name, c.model, 'data-k="' + name + '.model"');
    const how = name === "claude"
      ? '还没装？<a href="https://docs.claude.com/en/docs/claude-code/setup" target="_blank" rel="noopener">安装 Claude Code</a>，在终端里运行一次 <code>claude</code> 登录。翻译用的是你订阅里的额度。Opus / Sonnet 自动用 Claude Code 支持的最新版；要用刚出的新模型，先运行 <code>claude update</code>。'
      : (PR.cliModelDesc(st, "codex", c.model) ? PR.esc(PR.cliModelDesc(st, "codex", c.model)) + "<br>" : "") +
        '名单和 Codex 里 <code>/model</code> 看到的一样。还没装或要更新：<code>npm i -g @openai/codex@latest</code>，装好后运行一次 <code>codex</code> 登录。';
    return '<div class="grid2"><label class="field"><span>模型</span>' + model + "</label>" +
      '<label class="field"><span>推理强度</span>' + PR.reasoningSelect(c.reasoning_effort, 'data-k="' + name + '.reasoning_effort"') + "</label>" +
      '<label class="field"><span>命令</span><input class="input" data-k="' + name + '.command" value="' + PR.esc(c.command) + '"></label></div><p class="hint">' + how + "</p>";
  }
  /* 免费模型 = 本机开源模型 + 有免费额度的服务；付费 API 单独一张卡 */
  const API_KIND = { local: "free", free: "free", paid: "paid" };
  PR.apiKind = (presets, id) => API_KIND[((presets.find((x) => x.id === id) || {}).group) || "local"] || "free";
  function apiFields() {
    const o = st.cfg.openai;
    const kind = st.apiKind || PR.apiKind(st.presets, o.preset);
    const p = st.presets.find((x) => x.id === o.preset);
    const ollama = st.found && st.found.ollama;
    const modelChoices = (st.apiModels && st.apiModels.length ? st.apiModels : (p && p.models) || []);
    let model = '<div style="display:flex;gap:6px"><input class="input" style="min-width:0" data-k="openai.model" value="' + PR.esc(o.model) + '" list="modelList" placeholder="模型名"><button class="btn sm line" data-refresh-models="translation" title="从中转站读取模型">刷新模型</button></div><datalist id="modelList">' +
      modelChoices.map((m) => '<option value="' + PR.esc(m) + '">').join("") + "</datalist>";
    if (o.preset === "ollama" && ollama && ollama.models.length) {
      model = '<select class="input" data-k="openai.model">' + PR.opt(ollama.models.map((m) => [m, m]).concat(ollama.models.includes(o.model) || !o.model ? [] : [[o.model, o.model + "（没下载）"]]), o.model) + "</select>";
    }
    const saved = o.saved_keys || [];
    const tile = (x) => '<button data-preset="' + x.id + '" class="' + (o.preset === x.id ? "on" : "") + '">' + PR.esc(x.name) + (saved.includes(x.id) ? ' <span class="ok-dot" title="已存 Key"></span>' : "") + "</button>";
    const tiles = st.groups.filter(([g]) => (API_KIND[g] || "free") === kind).map(([g, label]) => '<div class="preset-group"><span>' + PR.esc(label) + "</span>" + st.presets.filter((x) => x.group === g).map(tile).join("") +
      (g === "local" ? '<button data-preset="" class="' + (!o.preset ? "on" : "") + '">自定义地址</button>' : "") + "</div>").join("");
    let note = p && p.note ? PR.esc(p.note) : "";
    if (o.preset === "ollama" && st.found) note = (ollama && ollama.running ? "Ollama 在运行，已下载 " + ollama.models.length + " 个模型。" : '<span class="bad">没检测到 Ollama（127.0.0.1:11434）。</span>') + note;
    return '<div class="preset-tiles grouped">' + tiles + "</div>" +
      (note || (p && p.key_url) ? '<p class="hint preset-note">' + note + (p && p.key_url ? ' <a href="' + p.key_url + '" target="_blank" rel="noopener">' + (p.key ? "获取 Key ↗" : "下载 ↗") + "</a>" : "") + "</p>" : "") +
      '<div class="grid2"><label class="field"><span>接口地址（base URL）</span><input class="input" data-k="openai.base_url" value="' + PR.esc(o.base_url) + '" placeholder="https://…/v1"></label>' +
      '<label class="field"><span>模型</span>' + model + "</label></div>" +
      (p && !p.key ? "" : '<label class="field"><span>API Key' + (o.has_key ? "（已保存，留空不改）" : "") + '</span><input class="input" type="password" data-k="openai.api_key" value="' + PR.esc(o.api_key) + '" placeholder="sk-…" autocomplete="off"></label>') +
      '<label class="field"><span>推理强度</span>' + PR.reasoningSelect(o.reasoning_effort, 'data-k="openai.reasoning_effort"') + "</label>" +
      '<label class="check" style="margin:0 0 10px"><input type="checkbox" data-k="openai.vision"' + (o.vision ? " checked" : "") + ">模型能看图（把原页图一起发过去，公式和表格更准）</label>" +
      '<p class="hint">Key 只存在本机的 config.json 里，只发给你填的这个地址。这里存的 Key，“问 AI”用同一家服务时也能直接用。</p>';
  }

  function pickPreset(s, id) {
    const p = s.presets.find((x) => x.id === id);
    const o = s.cfg.openai;
    o.preset = id;
    if (p) {
      o.base_url = p.base_url;
      const om = s.found && s.found.ollama && s.found.ollama.models;
      o.model = p.id === "ollama" && om && om.length && !om.includes(p.model) ? om[0] : p.model;
      o.vision = ["gemini", "openai", "anthropic"].includes(p.id);
    }
    const saved = (o.saved_keys || []).includes(o.preset);  // 每家的 Key 分开存，换回来不用重填
    o.api_key = saved ? "••••" : ""; o.has_key = saved;
  }

  function collect(state) {
    if (state.tab !== "engine") {  // 不在这一页时，用切页时存下的值
      const c = state.cfg, o = c.openai;
      return { engine: c.engine, batch_pages: c.batch_pages, concurrency: c.concurrency, auto_translate: c.auto_translate,
        claude: { model: c.claude.model, command: c.claude.command, reasoning_effort: c.claude.reasoning_effort },
        codex: { model: c.codex.model, command: c.codex.command, reasoning_effort: c.codex.reasoning_effort },
        openai: { preset: o.preset, base_url: o.base_url, model: o.model, api_key: o.api_key, reasoning_effort: o.reasoning_effort, vision: o.vision },
        deepread: { model: (c.deepread || {}).model || "", prompt: (c.deepread || {}).prompt || "" } };
    }
    const patch ={ engine: state.cfg.engine, claude: {}, codex: {}, openai: { preset: state.cfg.openai.preset } };
    PR.$$("[data-k]", dlg()).forEach((el) => {
      const [a, b] = el.dataset.k.split(".");
      const v = el.type === "checkbox" ? el.checked : el.value;
      if (b) patch[a][b] = v; else patch[a] = ["batch_pages", "concurrency"].includes(a) ? +v : v;
    });
    return patch;
  }
  PR.settingsTabs.engine = {
    render(s) {
      if (s.cfg.engine === "none") { s.cfg.engine = "claude"; s.cfg.auto_translate = false; }  // 旧的“不翻译”= 关掉自动翻译
      const e = s.cfg.engine;
      const cur = e === "openai" ? (s.apiKind || PR.apiKind(s.presets, s.cfg.openai.preset)) : e;
      const card = (k, title, text, extra) => '<button data-engine="' + k + '" class="' + (cur === k ? "on" : "") + '"><b>' + title + "</b>" + text + (extra || "") + "</button>";
      let h = '<p class="set-lead">导入论文后，用哪个模型在后台把它译成中文。</p><div class="engine-cards">' +
        card("claude", "Claude Code", "本机已登录的 Claude，不用 Key。会看原页图核对公式，译得最好。", badge("claude")) +
        card("codex", "Codex CLI", "本机已登录的 Codex（ChatGPT 账号），不用 Key。", badge("codex")) +
        card("free", "免费模型", "本机 Ollama 离线跑开源模型；或智谱、硅基流动、Gemini 等的免费模型。", '<span class="badge ok">免费</span>') +
        card("paid", "付费 API", "DeepSeek、通义、Kimi、OpenAI……一篇几毛钱。") + "</div>";
      if (e === "claude" || e === "codex") h += cliFields(e);
      else if (e === "openai") h += apiFields();
      return h + '<div class="test-line"><button class="btn sm line" id="testBtn">' + PR.icon("sparkle", "sm") + '试译一句</button><span class="test-result" id="testRes"></span></div>' +
        '<div class="settings-sec grid2">' +
        '<label class="field"><span>每次交给模型的页数</span><select class="input" data-k="batch_pages">' + PR.opt([[1, "1 页（最稳）"], [2, "2 页（推荐）"], [3, "3 页"], [4, "4 页"]], s.cfg.batch_pages) + "</select></label>" +
        '<label class="field"><span>同时翻译几批</span><select class="input" data-k="concurrency">' + PR.opt([[1, "1（本机 CLI 推荐）"], [2, "2"], [3, "3（API 推荐）"], [4, "4"], [6, "6（最快）"]], s.cfg.concurrency) + "</select></label></div>" +
        '<label class="check" style="margin-top:0"><input type="checkbox" data-k="auto_translate"' + (s.cfg.auto_translate ? " checked" : "") + ">导入后自动开始翻译</label>" +
        '<p class="hint" style="margin-top:12px">文献库位置：' + PR.esc(s.cfg.library_dir) + "</p>";
    },
    collect,
    sync(s) {
      const p = collect(s);
      Object.assign(s.cfg, { engine: p.engine, batch_pages: p.batch_pages ?? s.cfg.batch_pages, concurrency: p.concurrency ?? s.cfg.concurrency, auto_translate: p.auto_translate ?? s.cfg.auto_translate,
        claude: Object.assign({}, s.cfg.claude, p.claude), codex: Object.assign({}, s.cfg.codex, p.codex), openai: Object.assign({}, s.cfg.openai, p.openai) });
    },
    async click(e, s) {
      const card = e.target.closest("[data-engine]");
      if (card) {
        this.sync(s);
        const k = card.dataset.engine;
        if (k === "free" || k === "paid") {
          s.cfg.engine = "openai"; s.apiKind = k;
          if (PR.apiKind(s.presets, s.cfg.openai.preset) !== k || !s.cfg.openai.base_url) {  // 换到这一类的推荐项：免费先用本机 Ollama（在跑的话），否则智谱；付费用 DeepSeek
            const ol = s.found && s.found.ollama && s.found.ollama.running && s.found.ollama.models.length;
            pickPreset(s, k === "paid" ? "deepseek" : ol ? "ollama" : "zhipu");
          }
        } else { s.cfg.engine = k; s.apiKind = null; }
        if (s.cfg.engine === "openai" && s.cfg.concurrency < 2) s.cfg.concurrency = 3; if (s.cfg.engine !== "openai" && s.cfg.concurrency > 2) s.cfg.concurrency = 1; return true; }
      const pre = e.target.closest("[data-preset]");
      if (pre) { this.sync(s); pickPreset(s, pre.dataset.preset); return true; }
      if (e.target.closest("#testBtn")) {
        const res = PR.$("#testRes");
        res.className = "test-result"; res.innerHTML = '<span class="spin"></span> 正在让模型回一句话…';
        try {
          await PR.api("/api/config", { method: "POST", body: collect(s) });
          const r = await PR.api("/api/config/test", { method: "POST", body: { engine: s.cfg.engine } });
          res.className = "test-result " + (r.ok ? "ok" : "bad"); res.textContent = (r.ok ? "✓ " : "✗ ") + r.message;
        } catch (err) { res.className = "test-result bad"; res.textContent = err.message; }
      }
      const refresh = e.target.closest('[data-refresh-models="translation"]');
      if (refresh) {
        this.sync(s);
        refresh.disabled = true;
        try {
          const r = await PR.api("/api/models", { method: "POST", body: { scope: "translation", base_url: s.cfg.openai.base_url, api_key: s.cfg.openai.api_key } });
          s.apiModels = r.models || [];
          PR.toast("已读取 " + s.apiModels.length + " 个模型");
        } catch (err) { PR.toast("读取模型失败：" + PR.esc(err.message)); }
        refresh.disabled = false;
        return true;
      }
      return false;
    },
  };
})(window.PR);

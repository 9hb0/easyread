/* 设置 → 问 AI：阅读页右侧能选的模型。和“翻译”页同一套样子：
   上面一排卡片是名单里的模型（点一下设为新对话默认，右上角 ⋯ 改名、换模型、删除），最后一张是“添加”；
   添加或修改时，下面出现和“翻译”页一样的来源卡片（Claude Code / Codex / 免费模型 / 付费 API）和模型选择。
   API 的 Key 和翻译共用。 */
(function (PR) {
  "use strict";
  const T = PR.settingsTabs;
  const KIND_OF = { local: "free", free: "free", paid: "paid" };
  const kindOf = (s, m) => (m.engine === "openai" ? (m.preset ? KIND_OF[((s.presets.find((p) => p.id === m.preset) || {}).group) || "local"] || "free" : "custom") : m.engine);
  const hasKey = (s, preset) => (s.cfg.openai.saved_keys || []).includes(preset) || !!(s.chatKeys || {})[preset];
  const isApi = (k) => k === "free" || k === "paid" || k === "custom";

  function cardsHtml(s) {
    const list = s.chat.models;
    return '<div class="engine-cards mc-cards">' + list.map((m, i) => {
      const def = s.chat.default === m.id;
      const badge = m.ready === false ? '<span class="badge bad" title="' + PR.esc(m.hint || "") + '">' + PR.esc(m.hint || "还不能用") + "</span>"
        : def ? '<span class="badge ok">新对话默认</span>' : "";
      return '<div class="mc' + (def ? " on" : "") + (s.editing === m.id ? " editing" : "") + (m.ready === false ? " off" : "") + '" data-cm="default" data-i="' + i + '" role="button" tabindex="0" title="设为新对话默认">' +
        '<button class="mc-more" data-cm="more" data-i="' + i + '" title="改、删">' + PR.icon("more", "sm") + "</button>" +
        "<b>" + PR.esc(m.label || m.name) + "</b>" + PR.esc([m.source, m.detail].filter(Boolean).join(" · ")) + badge + "</div>";
    }).join("") +
      '<div class="mc add' + (s.editing === "new" ? " editing" : "") + '" data-cm="add" role="button" tabindex="0">' + PR.icon("plus") + "<b>添加模型</b>Claude、GPT、免费或付费 API</div></div>";
  }

  function formHtml(s) {
    const f = s.form;
    const card = (k, title, text) => '<button data-cmk="' + k + '" class="' + (f.kind === k ? "on" : "") + '"><b>' + title + "</b>" + text + "</button>";
    let h = '<div class="mc-form"><h4 class="set-h">' + (s.editing === "new" ? "添加一个模型" : "修改“" + PR.esc(f.name || autoName(s, f)) + "”") + "</h4>" +
      '<div class="engine-cards small">' +
      card("claude", "Claude Code", "本机已登录的 Claude") + card("codex", "Codex CLI", "本机已登录的 ChatGPT") +
      card("free", "免费模型", "本机 Ollama、智谱等") + card("paid", "付费 API", "DeepSeek、通义等") + card("custom", "自定义中转站", "BASE_URL、Key、模型自己填") + "</div>";
    if (f.kind === "claude" || f.kind === "codex") {
      h += '<label class="field"><span>模型</span>' + PR.cliModelInput(s, f.kind, f.model, 'id="cmModel"') + "</label>" +
        '<label class="field"><span>推理强度</span>' + PR.reasoningSelect(f.reasoning_effort, 'id="cmEffort"') + "</label>" +
        '<p class="hint">' + (f.kind === "claude" ? "Opus / Sonnet 自动用 Claude Code 支持的最新版；新模型出来后运行 <code>claude update</code>。回答时能自己读全文和原页图。"
          : (PR.cliModelDesc(s, "codex", f.model) ? PR.esc(PR.cliModelDesc(s, "codex", f.model)) + "<br>" : "") + "名单和 Codex 里 <code>/model</code> 看到的一样。提问时自动附上读者正在读的那页原图。") + "</p>";
    } else if (f.kind === "custom") {
      h += '<label class="field"><span>BASE_URL</span><input class="input" id="cmBaseUrl" value="' + PR.esc(f.base_url) + '" placeholder="https://中转站/v1"></label>' +
        '<div class="grid2"><label class="field"><span>模型</span><div style="display:flex;gap:6px"><input class="input" style="min-width:0" id="cmModel" list="cmModels" value="' + PR.esc(f.model) + '" placeholder="模型名"><button class="btn sm line" data-cm="refresh-models" title="从中转站读取模型">刷新</button></div><datalist id="cmModels">' + (f.models || []).map((x) => '<option value="' + PR.esc(x) + '">').join("") + "</datalist></label>" +
        '<label class="field"><span>推理强度</span>' + PR.reasoningSelect(f.reasoning_effort, 'id="cmEffort"') + "</label></div>" +
        '<label class="field"><span>API Key' + (f.hasKey ? "（已保存，留空不改）" : "") + '</span><input class="input" type="password" id="cmKey" value="' + PR.esc(f.key) + '" placeholder="sk-…" autocomplete="off"></label>' +
        '<label class="check"><input type="checkbox" id="cmVision"' + (f.vision ? " checked" : "") + ">模型能看图（把你正在读的那几页原页图一起发过去，公式和图表更准）</label>";
    } else {
      const groups = s.groups.filter(([g]) => (KIND_OF[g] || "free") === f.kind);
      h += '<div class="preset-tiles grouped">' + groups.map(([g, label]) => '<div class="preset-group"><span>' + PR.esc(label) + "</span>" +
        s.presets.filter((p) => p.group === g).map((p) => '<button data-cmp="' + p.id + '" class="' + (f.preset === p.id ? "on" : "") + '">' + PR.esc(p.name) +
          (hasKey(s, p.id) ? ' <span class="ok-dot" title="已存 Key"></span>' : "") + "</button>").join("") + "</div>").join("") + "</div>";
      const p = s.presets.find((x) => x.id === f.preset);
      if (p) {
        h += (p.note ? '<p class="hint preset-note">' + PR.esc(p.note) + (p.key_url ? ' <a href="' + p.key_url + '" target="_blank" rel="noopener">' + (p.key ? "获取 Key ↗" : "下载 ↗") + "</a>" : "") + "</p>" : "") +
          '<div class="grid2"><label class="field"><span>模型</span><div style="display:flex;gap:6px"><input class="input" style="min-width:0" id="cmModel" list="cmSugg" value="' + PR.esc(f.model) + '"><button class="btn sm line" data-cm="refresh-models" title="从中转站读取模型">刷新</button></div><datalist id="cmSugg">' +
          (p.models || []).map((x) => '<option value="' + PR.esc(x) + '">').join("") + "</datalist></label>" +
          (p.key ? '<label class="field"><span>API Key' + (hasKey(s, p.id) ? "（已保存，留空不改）" : "") + '</span><input class="input" type="password" id="cmKey" value="' + PR.esc(f.key || "") + '" placeholder="sk-…" autocomplete="off"></label>' : "<span></span>") + "</div>" +
          '<label class="field"><span>推理强度</span>' + PR.reasoningSelect(f.reasoning_effort, 'id="cmEffort"') + "</label>" +
          '<label class="check"><input type="checkbox" id="cmVision"' + (f.vision ? " checked" : "") + ">模型能看图（把你正在读的那几页原页图一起发过去，公式和图表更准）</label>";
      }
    }
    return h + '<label class="field"><span>显示的名字（可不填）</span><input class="input" id="cmName" value="' + PR.esc(f.name) + '" placeholder="' + PR.esc(autoName(s, f)) + '"></label>' +
      '<div class="cm-form-acts"><span class="hint">' + (isApi(f.kind) ? "Key 和“翻译”页共用，每家只填一次。" : "") + '</span><span class="grow"></span>' +
      '<button class="btn sm" data-cm="cancel">取消</button><button class="btn sm accent" data-cm="ok">' + (s.editing === "new" ? "加进名单" : "改好了") + "</button></div></div>";
  }
  function autoName(s, f) {
    if (f.kind === "claude" || f.kind === "codex") {
      const o = PR.cliModelOptions(s, f.kind, f.model, f.kind === "codex").find(([v]) => v === (f.model || ""));
      if (!o) return f.kind === "claude" ? "Claude" : "GPT";
      const inner = o[1].match(/^跟随.*（(.+)）$/);  // “跟随 Codex 默认（GPT-6-Astra）”→ GPT-6-Astra
      return inner ? inner[1] : o[1].replace(/（.*$/, "");
    }
    const p = s.presets.find((x) => x.id === f.preset);
    return f.model || (p ? p.name : "模型");
  }
  function readForm(s) {
    const f = s.form;
    if (!f) return;
    const v = (id) => { const el = PR.$("#" + id); return el ? el.value.trim() : null; };
    if (v("cmModel") !== null) f.model = v("cmModel");
    if (v("cmBaseUrl") !== null) f.base_url = v("cmBaseUrl");
    if (v("cmName") !== null) f.name = v("cmName");
    if (v("cmKey") !== null) f.key = v("cmKey");
    if (v("cmEffort") !== null) f.reasoning_effort = v("cmEffort");
    const vis = PR.$("#cmVision"); if (vis) f.vision = vis.checked;
  }
  function startForm(s, m) {
    s.form = m ? { id: m.id, kind: kindOf(s, m), model: m.model || "", preset: m.preset || "", base_url: m.base_url || "", name: m.name === m.label || m.name === "GPT" ? "" : m.name || "", key: "", hasKey: !!m.has_key, reasoning_effort: m.reasoning_effort || "", vision: !!m.vision, models: [] }
      : { id: "", kind: "claude", model: "opus", preset: "", base_url: "", name: "", key: "", hasKey: false, reasoning_effort: "", vision: false, models: [] };
  }

  T.chat = {
    render(s) {
      if (!s.chat) return '<p class="hint">读不到模型名单。</p>';
      return '<p class="set-lead">阅读页右侧“问 AI”可以选的模型。点一张卡片，设为新对话默认用的；读的时候在对话框左下角随时换。</p>' +
        cardsHtml(s) + (s.editing ? formHtml(s) : "") +
        '<p class="hint" style="margin-top:14px">每次提问会带上：你正在读的段落、你引用的几段、摘要和术语表。问到“标红的”“划线”“我的笔记”时，才会找出对应颜色的标记一起发过去。勾了“模型能看图”的 API 模型还会收到你正在读的那几页原页图；Claude Code 自己会读原页，Codex 每次自动附图。</p>';
    },
    sync(s) { readForm(s); },
    change(e, s) {
      if (e.target.id === "cmModel" && e.target.tagName === "SELECT") { readForm(s); return true; }  // 换了模型，说明和默认名字跟着变
      return false;
    },
    async click(e, s) {
      const k = e.target.closest("[data-cmk]");
      if (k && s.form) {
        readForm(s);
        const f = s.form, kind = k.dataset.cmk;
        if (kind === f.kind) return false;
        Object.assign(f, { kind, model: kind === "claude" ? "opus" : "", preset: kind === "custom" ? "" : f.preset, base_url: kind === "custom" ? "" : f.base_url, name: "", key: "", hasKey: false, reasoning_effort: "", vision: false, models: [] });
        if (isApi(kind)) {
          if (kind !== "custom") {
            const ol = s.found && s.found.ollama && s.found.ollama.running;
            f.preset = kind === "paid" ? "deepseek" : ol ? "ollama" : "zhipu";
            const preset = s.presets.find((p) => p.id === f.preset) || {};
            f.model = preset.model || ""; f.base_url = preset.base_url || "";
          }
        }
        return true;
      }
      const pb = e.target.closest("[data-cmp]");
      if (pb && s.form) { readForm(s); const p = s.presets.find((x) => x.id === pb.dataset.cmp) || {}; Object.assign(s.form, { preset: pb.dataset.cmp, base_url: p.base_url || "", model: p.model || "", key: "", name: "", hasKey: hasKey(s, pb.dataset.cmp), vision: !!p.vision, models: [] }); return true; }
      const b = e.target.closest("[data-cm]");
      if (!b) return false;
      const i = +b.dataset.i, list = s.chat.models, act = b.dataset.cm;
      if (act === "refresh-models") {
        readForm(s);
        const base = s.form.kind === "custom" ? s.form.base_url : ((s.presets.find((p) => p.id === s.form.preset) || {}).base_url || s.form.base_url);
        if (!base) { PR.toast("先填 BASE_URL"); return false; }
        try {
          const r = await PR.api("/api/models", { method: "POST", body: { scope: "chat", model_id: s.form.id, base_url: base, api_key: s.form.key || (s.form.hasKey ? "••••" : "") } });
          s.form.models = r.models || [];
          PR.toast("已读取 " + s.form.models.length + " 个模型");
        } catch (err) { PR.toast("读取模型失败：" + PR.esc(err.message)); }
        return true;
      }
      if (act === "more") {
        const m = list[i];
        PR.menu(b, [
          { label: "修改", icon: "edit", fn: () => { readForm(s); s.editing = m.id; startForm(s, m); PR.settingsRender(); } },
          { label: "往前挪", icon: "back", disabled: i === 0, fn: () => { list.splice(i - 1, 0, list.splice(i, 1)[0]); PR.settingsRender(); } },
          "-",
          { label: "从名单里删掉", icon: "trash", fn: async () => {
            if (list.length <= 1) return PR.toast("至少留一个模型");
            if (!(await PR.confirm({ title: "删掉“" + (m.label || m.name) + "”？", body: "只是从“问 AI”的名单里拿掉，已有的对话不受影响。", ok: "删掉", danger: true }))) return;
            const at = list.indexOf(m); if (at >= 0) list.splice(at, 1);
            if (s.chat.default === m.id) s.chat.default = list[0].id;
            if (s.editing === m.id) { s.editing = null; s.form = null; }
            PR.settingsRender();
          } },
        ]);
        return false;
      }
      if (act === "default") { if (s.chat.default === list[i].id) return false; s.chat.default = list[i].id; }
      if (act === "add") { readForm(s); s.editing = "new"; startForm(s); }
      if (act === "cancel") { s.editing = null; s.form = null; }
      if (act === "ok") {
        readForm(s);
        const f = s.form, api = isApi(f.kind);
        const p = s.presets.find((x) => x.id === f.preset);
        if (api && !f.model) { PR.toast("填一个模型名"); return false; }
        if (f.kind === "custom" && !f.base_url) { PR.toast("填 BASE_URL"); return false; }
        if (api && p && p.key && !hasKey(s, f.preset) && !f.key) { PR.toast("这家要填 API Key"); return false; }
        if (api && f.key) (s.chatKeys = s.chatKeys || {})[f.preset] = f.key;
        const name = f.name || autoName(s, f);
        const m = { engine: api ? "openai" : f.kind, preset: api && f.kind !== "custom" ? f.preset : "", base_url: api && f.kind === "custom" ? f.base_url : "", api_key: api ? (f.key || (f.hasKey ? "••••" : "")) : "", model: f.model, reasoning_effort: f.reasoning_effort || "", vision: api ? !!f.vision : false, name, label: name,
          source: api ? (f.kind === "custom" ? "自定义中转站" : p ? p.name : "API") : f.kind === "claude" ? "Claude Code" : "Codex CLI", detail: f.model || "跟随 Codex 默认", ready: true };
        if (s.editing === "new") list.push(Object.assign(m, { id: "m" + Date.now().toString(36) }));
        else Object.assign(list.find((x) => x.id === s.editing), m);
        s.editing = null; s.form = null;
      }
      return true;
    },
  };
})(window.PR);

/* “精读论文”设置：模型复用问 AI 的名单，Key 仍由名单里的模型配置提供。 */
(function (PR) {
  "use strict";
  const T = PR.settingsTabs;

  function models(s) {
    return (s.chat && s.chat.models) || [];
  }

  T.deepread = {
    render(s) {
      const d = s.cfg.deepread || {};
      const ob = s.cfg.obsidian || (s.cfg.obsidian = { vault: "", folder: "EasyRead 精读", auto: true });
      const wk = s.cfg.wiki || (s.cfg.wiki = { vault: "" });
      const chat = s.chat || {};
      const def = chat.default || "";
      const opts = '<option value="">跟随“问 AI”的默认模型' + (def ? "（" + PR.esc(def) + "）" : "") + "</option>" +
        models(s).map((m) => '<option value="' + PR.esc(m.id) + '"' + (m.id === d.model ? " selected" : "") + ">" + PR.esc(m.label || m.name || m.model || m.id) + " · " + PR.esc(m.id) + (m.ready === false ? "（未就绪）" : "") + "</option>").join("");
      const vaults = s.vaults || [];
      const vaultList = vaults.length ? '<datalist id="obVaultList">' + vaults.map((v) => '<option value="' + PR.esc(v) + '">').join("") + "</datalist>" : "";
      return '<p class="set-lead">主动生成一份论文精读文档，不会随导入或翻译自动执行。</p>' +
        '<label class="field"><span>精读模型</span><select class="input" data-k="deepread.model">' + opts + "</select></label>" +
        '<label class="field"><span>精读提示词</span><textarea class="input" data-k="deepread.prompt" rows="12" placeholder="告诉模型你想重点看什么…">' + PR.esc(d.prompt || "") + "</textarea></label>" +
        '<p class="hint">精读优先使用已翻译中文，未翻译部分使用英文原文。最终文档最多 5000 字，生成失败不会覆盖旧文档。</p>' +
        '<div class="settings-sec"><label class="field"><span>Obsidian 库路径（vault）</span><input class="input" data-k="obsidian.vault" list="obVaultList" value="' + PR.esc(ob.vault || "") + '" placeholder="本机 Obsidian 库的文件夹">' + vaultList + "</label>" +
        '<div class="grid2"><label class="field"><span>库里的文件夹</span><input class="input" data-k="obsidian.folder" value="' + PR.esc(ob.folder || "EasyRead 精读") + '"></label>' +
        '<label class="check" style="align-self:end;margin:0"><input type="checkbox" data-k="obsidian.auto"' + (ob.auto ? " checked" : "") + ">精读生成后自动同步</label></div>" +
        '<label class="check" style="margin:2px 0 10px"><input type="checkbox" data-k="obsidian.classify"' + (ob.classify ? " checked" : "") + ">按主题自动归类：让模型从库里带编号的文件夹（00_、01_…）里选一个</label>" +
        '<div class="test-line"><button class="btn sm line" id="obSyncAllBtn">' + PR.icon("upload", "sm") + '立即全部同步</button><span class="test-result" id="obSyncAllRes"></span></div>' +
        '<p class="hint">把精读文档写进知识库：公式转成 $…$，按论文标题命名，带作者、年份、出处等属性；内容变了会更新，改了标题会清掉旧文件。归类在精读生成时进行，已入库的论文要重新归类，运行 easyread obsidian --classify。</p></div>' +
        '<div class="settings-sec"><label class="field"><span>论文入库知识库路径</span><input class="input" data-k="wiki.vault" value="' + PR.esc(wk.vault || "") + '" placeholder="例如 D:\\Agent\\wiki\\3D&4D"></label>' +
        '<p class="hint">填了以后，阅读页的精读面板会多一个“入知识库”按钮：把精读文档和原论文 PDF 交给这个知识库自带的 paper-ingest 入库流程——入库模型整理候选、六项完整性筛查、通过后发布并更新索引，和手动跑 workflow.ps1 的 ingestion 一样。本机需要装 uv，知识库里配好入库模型。</p></div>';
    },
    sync(s, root) {
      const d = s.cfg.deepread || (s.cfg.deepread = {});
      const model = PR.$('[data-k="deepread.model"]', root || document);
      const prompt = PR.$('[data-k="deepread.prompt"]', root || document);
      if (model) d.model = model.value;
      if (prompt) d.prompt = prompt.value;
      const ob = s.cfg.obsidian || (s.cfg.obsidian = {});
      const ov = PR.$('[data-k="obsidian.vault"]', root || document);
      const of = PR.$('[data-k="obsidian.folder"]', root || document);
      const oa = PR.$('[data-k="obsidian.auto"]', root || document);
      const oc = PR.$('[data-k="obsidian.classify"]', root || document);
      if (ov) ob.vault = ov.value.trim();
      if (of) ob.folder = of.value.trim() || "EasyRead 精读";
      if (oa) ob.auto = oa.checked;
      if (oc) ob.classify = oc.checked;
      const wk = s.cfg.wiki || (s.cfg.wiki = {});
      const wv = PR.$('[data-k="wiki.vault"]', root || document);
      if (wv) wk.vault = wv.value.trim();
    },
    async click(e, s) {
      if (!e.target.closest("#obSyncAllBtn")) return false;
      this.sync(s);
      const res = PR.$("#obSyncAllRes");
      res.className = "test-result"; res.innerHTML = '<span class="spin"></span> 正在同步…';
      try {
        const ob = s.cfg.obsidian || {};
        const r = await PR.api("/api/obsidian/sync", { method: "POST", body: { vault: ob.vault || "", folder: ob.folder || "" } });
        if (!r.configured) { res.className = "test-result bad"; res.textContent = "✗ 先填上面的库路径"; }
        else { res.className = "test-result ok"; res.textContent = "✓ 写入 " + r.written + " 篇，未变 " + r.unchanged + " 篇" + (r.error ? "，出错 " + r.error + " 篇" : ""); }
      } catch (err) { res.className = "test-result bad"; res.textContent = err.message; }
      return false;
    },
  };
})(window.PR);

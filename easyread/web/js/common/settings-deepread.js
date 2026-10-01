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
      const chat = s.chat || {};
      const def = chat.default || "";
      const opts = '<option value="">跟随“问 AI”的默认模型' + (def ? "（" + PR.esc(def) + "）" : "") + "</option>" +
        models(s).map((m) => '<option value="' + PR.esc(m.id) + '"' + (m.id === d.model ? " selected" : "") + ">" + PR.esc(m.label || m.name || m.model || m.id) + " · " + PR.esc(m.id) + (m.ready === false ? "（未就绪）" : "") + "</option>").join("");
      return '<p class="set-lead">主动生成一份论文精读文档，不会随导入或翻译自动执行。</p>' +
        '<label class="field"><span>精读模型</span><select class="input" data-k="deepread.model">' + opts + "</select></label>" +
        '<label class="field"><span>精读提示词</span><textarea class="input" data-k="deepread.prompt" rows="12" placeholder="告诉模型你想重点看什么…">' + PR.esc(d.prompt || "") + "</textarea></label>" +
        '<p class="hint">精读优先使用已翻译中文，未翻译部分使用英文原文。最终文档最多 5000 字，生成失败不会覆盖旧文档。</p>';
    },
    sync(s, root) {
      const d = s.cfg.deepread || (s.cfg.deepread = {});
      const model = PR.$('[data-k="deepread.model"]', root || document);
      const prompt = PR.$('[data-k="deepread.prompt"]', root || document);
      if (model) d.model = model.value;
      if (prompt) d.prompt = prompt.value;
    },
  };
})(window.PR);

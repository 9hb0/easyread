/* Claude Code / Codex 的模型下拉框，“翻译”和“问 AI”两页共用。
   名单来自 /api/engines 的 models（后端 cli_models.py）：Codex 就是它 /model 里列的那些；Claude 的别名带上实际版本。 */
(function (PR) {
  "use strict";
  const FALLBACK = { claude: { models: [{ id: "opus", name: "Opus", desc: "最强" }, { id: "sonnet", name: "Sonnet", desc: "快、省" }, { id: "haiku", name: "Haiku", desc: "最快最省" }] },
    codex: { default: "", models: [] } };
  const lists = (s) => (s.models || FALLBACK);
  const EFFORTS = [["", "不启用"], ["low", "低"], ["medium", "中"], ["high", "高"], ["xhigh", "超高"], ["max", "最大"]];

  /* 下拉框的选项：[[value, label]]。withDefault：最前面加一项“跟随 CLI 默认” */
  PR.cliModelOptions = function (s, engine, value, withDefault) {
    const L = lists(s)[engine] || FALLBACK[engine];
    let opts;
    if (engine === "claude") {
      opts = L.models.map((m) => [m.id, (m.actual || "Claude " + m.name) + "（" + m.desc + (m.actual ? "" : "，自动用最新版") + "）"]);
      if (withDefault) opts.unshift(["", "跟随 Claude Code 默认"]);
    } else {
      opts = L.models.map((m) => [m.id, m.name]);
      const dn = (L.models.find((m) => m.id === L.default) || {}).name || L.default;
      if (withDefault || !opts.length) opts.unshift(["", "跟随 Codex 默认" + (dn ? "（" + dn + "）" : "")]);
    }
    if (value && !opts.some(([v]) => v === value)) opts.push([value, value]);
    return opts;
  };
  PR.cliModelSelect = (s, engine, value, attrs, withDefault) =>
    "<select class=\"input\" " + attrs + ">" + PR.opt(PR.cliModelOptions(s, engine, value, withDefault), value) + "</select>";
  PR.cliModelInput = function (s, engine, value, attrs) {
    const id = "cli-models-" + engine;
    const opts = PR.cliModelOptions(s, engine, value, false).map(([v]) => v).filter(Boolean);
    return "<input class=\"input\" " + attrs + " list=\"" + id + "\" value=\"" + PR.esc(value || "") + "\" placeholder=\"可手填模型名\"><datalist id=\"" + id + "\">" +
      opts.map((v) => "<option value=\"" + PR.esc(v) + "\">").join("") + "</datalist>";
  };
  PR.reasoningSelect = (value, attrs) => "<select class=\"input\" " + (attrs || "") + ">" + PR.opt(EFFORTS, value || "") + "</select>";
  /* 选中项的说明：Codex 把 /model 里那句介绍也显示出来 */
  PR.cliModelDesc = function (s, engine, value) {
    if (engine !== "codex") return "";
    const L = lists(s).codex || FALLBACK.codex;
    const m = L.models.find((x) => x.id === (value || L.default));
    return m ? m.name + "：" + m.desc : "";
  };
})(window.PR);

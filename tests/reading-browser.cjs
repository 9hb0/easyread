/* Dynamic reader tests. Uses only an ephemeral fixture server, never the user's library/server.
   Run: node --test tests/reading-browser.cjs
   Optional: PLAYWRIGHT_MODULE and READING_BROWSER_EXE override the bundled runtime/browser. */
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { spawnSync } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ||
  "C:/Users/Axiaoxin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const root = path.resolve(__dirname, "..");
const web = path.join(root, "easyread", "web");
const scripts = ["common/util", "common/features", "common/markup", "reader/store", "reader/render",
  "reader/anchor", "reader/reading", "reader/margin", "reader/pageview", "reader/actions", "reader/panels", "reader/guide"];
const html = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  ${["base", "reader", "reader-panels"].map((s) => `<link rel="stylesheet" href="/web/css/${s}.css">`).join("")}
  <link rel="stylesheet" href="/web/vendor/katex/katex.min.css"></head><body>
  <header class="topbar" id="bar"><a class="btn brand-back" id="backBtn"></a>${["drawer", "pages", "notes", "chat", "deepread", "reading", "guide", "settings"].map((s) =>
    `<button class="btn" data-act="${s}" ${s === "reading" ? 'style="display:none"' : ""}>${s}</button>`).join("")}
    <span class="save-state"><span></span></span><span id="jobState"></span></header>
  <div id="stage"><article id="paper"></article><aside id="margin"></aside></div>
  <aside id="drawer"><div class="drawer-tabs"></div><div class="drawer-body"></div></aside><div id="scrim"></div>
  <section id="pageview" class="side-panel"><div class="pv-head"><button data-pv="close">关闭</button>
    <span class="pv-label"></span><label class="pv-follow"><input type="checkbox" checked></label><a data-pv="pdf">PDF</a></div>
    <div class="pv-scroll"><div class="pv-page"><img><div class="pv-hl"></div></div></div></section>
  <section id="notespanel" class="side-panel"></section><section id="chatpanel" class="side-panel"></section>
  <section id="deepreadpanel" class="side-panel"></section>
  <section id="guidepanel" class="side-panel"><div class="side-panel-grip" data-side-grip tabindex="0"></div></section>
  <div id="settings"></div><div id="blockbar" class="float"></div><div id="selbar" class="float"></div>
  <div id="popover" class="popover float"></div><div id="toast"></div>
  <script src="/web/vendor/katex/katex.min.js"></script>
  ${scripts.map((s) => `<script src="/web/js/${s}.js"></script>`).join("")}
  <script>window.ready = PR.load().then(() => { PR.renderPaper(); PR.applyMarks(); PR.renderMargin(); });</script>
  </body></html>`;
const image = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><rect width="400" height="600" fill="white"/></svg>');
let server, browser, base, current, requests;

// Fixture hashes follow the persisted FNV/UTF-16 contract independently of browser code.
function hash(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619) >>> 0;
  return h.toString(16).padStart(8, "0");
}
function textOf(b) {
  if (b.type === "list") return b.items.map((i) => i.zh || "").join("\n");
  if (["table", "figure"].includes(b.type)) return b.caption_zh || "";
  return ["para", "heading", "references", "note"].includes(b.type) ? b.zh || "" : "";
}
function sourceText(b) {
  if (Object.hasOwn(b, "source_en")) return typeof b.source_en === "string" ? b.source_en : "";
  if (b.type === "list") return (b.items || []).map((i) => String(i.en || "")).join("\n");
  const text = ["table", "figure"].includes(b.type) ? b.caption_en : b.en;
  return typeof text === "string" ? text : "";
}
function contentHash(blocks, page) {
  const cell = (value) => value == null ? "" : String(value);
  return hash(blocks.filter((b) => b.page === page).map((b) => {
    const fields = [String(b.id || ""), String(b.type || ""), textOf(b), cell(b.tex), cell(b.tag), sourceText(b)];
    if (b.type === "table") for (const row of [...(b.head || []), ...(b.rows || [])]) fields.push(row.map(cell).join("\u241f"));
    return fields.join("\u241f");
  }).join("\u241e"));
}
function pythonContentHash(blocks, page) {
  const result = spawnSync(process.env.READING_PYTHON || "python", ["-c",
    "import json,sys; from easyread.reading import page_content_hash; data=json.load(sys.stdin); print(page_content_hash(data['blocks'],data['page']))"],
    { cwd: root, input: JSON.stringify({ blocks, page }), encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  return result.stdout.trim();
}
function ref(b) { return { page: b.page, block_id: b.id }; }
function hl(b, quote, category = "method", occurrence = 0, id = "h-" + b.id) {
  // Saved backend highlights bind to their owning block; only annotations/insights carry refs.
  return { id, block_id: b.id, category, quote, occurrence };
}
function ann(b, quote, content, basis = "source_supported", id = "a-" + b.id, occurrence = 0) {
  return { id, block_id: b.id, anchor_quote: quote, occurrence, type: "concept", content, basis, source_refs: [ref(b)] };
}
function para(id, zh, page = 1) { return { id, type: "para", page, zh, en: "Original " + id, source_en: "Original " + id }; }
function paper(blocks, total = 3, done = [...new Set(blocks.map((b) => b.page))]) {
  const p = { meta: { title_zh: "浏览器精读测试", page_count: total,
    pages: Array.from({ length: total }, (_, i) => ({ n: i + 1, img: `pages/${i + 1}.svg` })) }, blocks,
    translation: { done_pages: done, target_pages: done, source_versions: {} },
    reading: { version: 2, pages: {}, insights: [] }, reading_guide: { version: 2, status: "complete" } };
  for (const n of done) {
    p.translation.source_versions[n] = "source-" + n;
    p.reading.pages[n] = { status: "completed", version: 2, source_version: "source-" + n,
      zh_hash: hash(blocks.filter((b) => b.page === n).map(textOf).filter(Boolean).join("\n")), content_hash: contentHash(blocks, n) };
  }
  return p;
}
function state(p, reader = {}) {
  return { token: "fixture-token", paper: p, reader, discussion: { entries: [] }, item: {}, job: {}, versions: { paper: "v1" }, engine: "none",
    layout: Object.fromEntries(p.blocks.map((b) => [b.id, { page: b.page, box: [.1, .2, .8, .35] }])) };
}
function pythonQuality(p) {
  const result = spawnSync(process.env.READING_PYTHON || "python", ["-B", "-c",
    "import json,sys; from easyread.reading_quality import rebuild_quality,quality_input_hash; p=json.load(sys.stdin); rebuild_quality(p); print(json.dumps({'quality':p['reading']['quality'],'hash':quality_input_hash(p)},ensure_ascii=False))"],
    { cwd: root, input: JSON.stringify(p), encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  return JSON.parse(result.stdout);
}
function duplicatePaper() {
  const quote = "关键结论😀：本方法在明确条件下改善了性能。";
  const a = para("canonical😀", quote, 1), b = para("duplicate😀", quote, 2);
  a.highlights = [{ ...hl(a, quote, "finding"), importance: 5 }];
  b.highlights = [{ ...hl(b, quote, "finding"), importance: 4 }];
  b.annotations = [ann(b, quote, "重复位置的真实批注")];
  const p = paper([a, b], 2); p.reading.quality = pythonQuality(p).quality;
  return p;
}
async function openFilter(page) {
  if (!await page.locator("#popover.open .rf-list").count()) await page.locator('[data-act="reading"]').click();
}
async function setDensity(page, density) {
  await openFilter(page);
  const button = page.locator(`[data-density="${density}"]`);
  assert.equal(await button.count(), 1, "density control must be available");
  await button.click();
}
async function assertStats(page, displayed, total, categories = {}) {
  await openFilter(page);
  assert.equal(await page.locator(".rf-summary").count(), 1, "statistics must state their denominator");
  assert.match(await page.locator(".rf-summary").innerText(), new RegExp(`当前显示\\s*${displayed}\\s*/\\s*全部有效候选\\s*${total}`));
  for (const [category, numbers] of Object.entries(categories)) {
    assert.equal((await page.locator(`.rf-row[data-cat="${category}"] b`).innerText()).replace(/\s/g, ""), numbers);
  }
}
async function load(p, opts = {}) {
  current = state(p, opts.reader || {}); requests = [];
  const context = await browser.newContext({ viewport: opts.mobile ? { width: 390, height: 844 } : { width: 1500, height: 1000 } });
  const page = await context.newPage(); const errors = [];
  if (opts.filter) await context.addInitScript((filter) => {
    if (localStorage.getItem("pr-reading-filter") === null) localStorage.setItem("pr-reading-filter", JSON.stringify(filter));
  }, opts.filter);
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(base + "/read/fixture");
  await page.evaluate(() => window.ready);
  await page.waitForLoadState("networkidle");
  assert.deepEqual(errors, [], "production scripts must initialize without browser errors");
  page.uiRequests = [];
  page.on("request", (req) => { if (!req.url().endsWith("/favicon.ico")) page.uiRequests.push(req.url()); });
  page.errors = errors;
  page.dispose = async () => { assert.deepEqual(errors, [], "no browser errors"); await context.close(); };
  return page;
}

before(async () => {
  server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://fixture");
    if (url.pathname === "/read/fixture") { res.setHeader("Content-Type", "text/html; charset=utf-8"); return res.end(html); }
    if (url.pathname === "/api/p/fixture/state") { res.setHeader("Content-Type", "application/json"); return res.end(JSON.stringify(current)); }
    if (url.pathname === "/api/p/fixture/enrich") {
      let body = ""; for await (const chunk of req) body += chunk;
      requests.push({ url: url.pathname, method: req.method, token: req.headers["x-token"], body: JSON.parse(body) });
      res.setHeader("Content-Type", "application/json"); return res.end('{"id":"fixture-job","kind":"enrich","state":"queued"}');
    }
    if (url.pathname.startsWith("/p/fixture/pages/")) { res.setHeader("Content-Type", "image/svg+xml"); return res.end(image); }
    if (url.pathname.startsWith("/web/")) {
      const file = path.resolve(web, decodeURIComponent(url.pathname.slice(5)));
      if (file.startsWith(web + path.sep) && fs.existsSync(file)) {
        res.setHeader("Content-Type", file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "application/javascript" : "application/octet-stream");
        return res.end(fs.readFileSync(file));
      }
    }
    if (url.pathname === "/favicon.ico") { res.writeHead(204); return res.end(); }
    res.writeHead(404); res.end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = "http://127.0.0.1:" + server.address().port;
  const executablePath = process.env.READING_BROWSER_EXE || [
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  ].find((p) => fs.existsSync(p));
  assert.ok(executablePath, "install Chrome/Edge or set READING_BROWSER_EXE");
  browser = await chromium.launch({ executablePath, headless: true });
});
after(async () => { if (browser) await browser.close(); if (server) await new Promise((resolve) => server.close(resolve)); });

test("old papers with zero marks can queue reading without retranslating", async () => {
  const b = para("old", "已有译文，不重译。"); const p = paper([b]); delete p.reading; delete p.reading_guide;
  const page = await load(p);
  assert.ok(await page.locator('[data-act="reading"]').isVisible());
  await page.locator('[data-act="reading"]').click();
  await page.locator('[data-f="enrich"]').click();
  await page.waitForFunction(() => document.getElementById("toast").textContent.includes("排队"));
  assert.deepEqual(requests, [{ url: "/api/p/fixture/enrich", method: "POST", token: "fixture-token", body: { pages: [1] } }]);
  assert.equal(await page.locator("#b-old .zh").innerText(), "已有译文，不重译。");
  await page.dispose();
});

test("saved highlights without source_refs render and derive only their own original-page jump", async () => {
  const b = para("saved-highlight", "真实保存协议中的重点。", 2);
  b.highlights = [hl(b, "重点")];
  b.annotations = [ann(b, "重点", "缺失来源的批注不能显示")];
  delete b.annotations[0].source_refs;
  const p = paper([b]);
  p.reading.insights = [{ id: "no-refs", type: "method", content: "缺失来源的 insight 不能显示", block_id: b.id }];
  const page = await load(p);
  assert.equal(await page.evaluate(() => PR.readingPageValid(2)), true);
  assert.equal(await page.locator("mark.sem").count(), 1);
  assert.equal(await page.locator(".card.reading").count(), 0);
  assert.equal(await page.evaluate(() => Object.hasOwn(PR.state.paper.blocks[0].highlights[0], "source_refs")), false,
    "derived UI refs must not rewrite the saved highlight");
  await page.locator("mark.sem").click();
  assert.ok(!(await page.locator("#popover").innerText()).includes("缺失来源的批注"));
  const source = page.locator("#popover [data-reading-source]");
  assert.equal(await source.count(), 1);
  assert.equal(await source.getAttribute("data-page"), "2");
  assert.equal(await source.getAttribute("data-reading-source"), b.id);
  await source.click();
  assert.equal(await page.evaluate(() => PR.side), "pages");
  assert.equal(await page.locator(".pv-label").innerText(), "第 2 / 3 页");
  assert.ok(await page.locator(".pv-hl").evaluate((el) => el.classList.contains("on")));
  await page.locator('[data-act="guide"]').click();
  assert.ok(!(await page.locator("#guidepanel").innerText()).includes("缺失来源的 insight"));
  await page.dispose();
});

for (const [name, corrupt] of [
  ["stale", (p) => p.reading.pages[1].status = "stale"],
  ["failed", (p) => p.reading.pages[1].status = "failed"],
  ["translation hash mismatch", (p) => p.blocks[0].zh += "更新"],
  ["missing content hash", (p) => delete p.reading.pages[1].content_hash],
  ["content hash mismatch", (p) => p.reading.pages[1].content_hash = "old-snapshot"],
  ["reading schema mismatch", (p) => p.reading.version = 1],
  ["page schema mismatch", (p) => p.reading.pages[1].version = 1],
  ["source version mismatch", (p) => p.translation.source_versions[1] = "new-source"],
  ["missing version binding", (p) => delete p.reading.pages[1].source_version],
  ["missing source version", (p) => delete p.translation.source_versions[1]],
]) test(name + " suppresses marks and annotation cards", async () => {
  const b = para("bad", "当前关键短语。"); b.highlights = [hl(b, "关键短语")]; b.annotations = [ann(b, "关键短语", "旧批注不该显示")];
  const p = paper([b]); corrupt(p); const page = await load(p);
  assert.equal(await page.locator("mark.sem").count(), 0);
  assert.equal(await page.locator(".card.reading").count(), 0);
  assert.ok(await page.locator('[data-act="reading"]').isVisible(), "retry remains available");
  await page.dispose();
});

test("exact source occurrence maps lists, line breaks, Markdown, Unicode and math reversibly", async () => {
  const list = { id: "list", type: "list", page: 1, source_en: "First\nSecond", items: [{ zh: "重复目标" }, { zh: "重复目标" }] };
  list.highlights = [hl(list, "重复目标", "method", 1, "second"), hl(list, "标目", "evidence", 0, "missing")];
  const b = para("fmt", "中文😀：**关键方法**\n跨行内容、`代码`，*斜体*。公式 $x^2$ 后的证据。另有 \\$ 字符。");
  b.highlights = [hl(b, "**关键方法**\n跨行内容", "method", 0, "cross"),
    hl(b, "`代码`", "innovation", 0, "code"), hl(b, "*斜体*", "background", 0, "em"),
    hl(b, "公式 $x^2$ 后的证据", "evidence", 0, "math-adjacent"), hl(b, "中文😀", "problem", 0, "unicode")];
  const page = await load(paper([list, b]));
  assert.equal(await page.locator("#b-list li:first-child mark.sem").count(), 0);
  assert.equal(await page.locator("#b-list li:nth-child(2) mark.sem").innerText(), "重复目标");
  assert.equal((await page.locator('mark[data-hl="cross"]').allTextContents()).join(""), "关键方法跨行内容");
  assert.equal(await page.locator('mark[data-hl="code"]').innerText(), "代码");
  assert.equal(await page.locator('mark[data-hl="em"]').innerText(), "斜体");
  assert.equal(await page.locator('mark[data-hl="unicode"]').innerText(), "中文😀");
  assert.equal(await page.locator(".katex mark.sem, mark.sem .katex, .katex-mathml mark").count(), 0);
  const snapshot = await page.locator("#b-fmt .zh").evaluate((el) => ({ text: el.innerText, math: el.querySelector(".katex").outerHTML }));
  await page.evaluate(() => PR.saveReadingFilter({ off: true, only: "" }));
  assert.deepEqual(await page.locator("#b-fmt .zh").evaluate((el) => ({ text: el.innerText, math: el.querySelector(".katex").outerHTML })), snapshot);
  await page.evaluate(() => { PR.saveReadingFilter({ off: false, only: "" }); PR.applyReadingMarks(); PR.applyMarks(); });
  assert.equal(await page.locator("mark.sem mark.sem").count(), 0);
  assert.equal(await page.locator('mark[data-hl="cross"]').count(), 2);
  const selected = await page.locator("#b-fmt .zh").evaluate((el) => { const r = document.createRange(); r.selectNodeContents(el); getSelection().removeAllRanges(); getSelection().addRange(r); return getSelection().toString(); });
  assert.ok(selected.includes("关键方法") && selected.includes("跨行内容"), "selection still works");
  await page.dispose();
});

test("conflicts, false occurrences and invalid block/source bindings never produce marks/cards", async () => {
  const b = para("invalid", "相互冲突和独立短语。");
  b.highlights = [hl(b, "相互冲突", "method", 0, "left"), hl(b, "冲突和", "problem", 0, "right"),
    { ...hl(b, "独立短语"), block_id: "other" }, hl(b, "独立短语", "evidence", -1), { ...hl(b, "独立短语"), source_refs: [] }];
  b.annotations = [{ ...ann(b, "独立短语", "bad"), block_id: "other" }, ann(b, "不存在", "bad"),
    { ...ann(b, "独立短语", "bad"), source_refs: [{ page: 2, block_id: b.id }] }];
  const page = await load(paper([b]));
  assert.equal(await page.locator("mark.sem").count(), 0);
  assert.equal(await page.locator(".card.reading").count(), 0);
  await page.dispose();
});

test("contradictory source IDs and false English quotes are rejected", async () => {
  const b = para("source-check", "第一处重点与第二处重点。");
  b.highlights = [{ ...hl(b, "第一处重点"), source_refs: [{ page: 1, block: b.id, block_id: "other" }] },
    { ...hl(b, "第二处重点"), source_refs: [{ ...ref(b), source_quote: "invented English source" }] }];
  b.annotations = [{ ...ann(b, "第二处重点", "虚假引用"), source_refs: [{ ...ref(b), quote: "not in original" }] }];
  const page = await load(paper([b]));
  assert.equal(await page.locator("mark.sem").count(), 0);
  assert.equal(await page.locator(".card.reading").count(), 0);
  await page.dispose();
});

test("quotes across list separators remain reversible; formula interiors and missing rendered spaces are never guessed", async () => {
  const list = { id: "cross-list", type: "list", page: 1, source_en: "List source", items: [{ zh: "第一项末尾" }, { zh: "第二项开头" }] };
  list.highlights = [hl(list, "末尾\n第二项", "method", 0, "list-boundary")];
  const b = para("unsafe-formula", "公式 $x^2$ 旁边安全文本。");
  b.highlights = [hl(b, "x^2", "method", 0, "inside"), hl(b, "安全文本", "evidence", 0, "safe")];
  const page = await load(paper([list, b]));
  assert.deepEqual(await page.locator('mark[data-hl="list-boundary"]').allTextContents(), ["末尾", "第二项"]);
  assert.equal(await page.locator('mark[data-hl="inside"]').count(), 0);
  assert.equal(await page.locator('mark[data-hl="safe"]').innerText(), "安全文本");
  await page.evaluate(() => { document.querySelector("#b-unsafe-formula .zh").appendChild(document.createTextNode("额外文本")); PR.applyReadingMarks("unsafe-formula"); });
  assert.equal(await page.locator("#b-unsafe-formula mark.sem").count(), 0, "a changed DOM is not mapped by approximation");
  await page.dispose();
});

test("annotation occurrence, same-range deduplication and linked cards match the right repeated phrase", async () => {
  const b = para("repeat", "重复概念和重复概念。");
  b.highlights = [hl(b, "重复概念", "method", 1, "repeat-h"), hl(b, "重复概念", "method", 1, "duplicate")];
  b.annotations = [ann(b, "重复概念", "第一处解释", "author_statement", "first-a", 0),
    ann(b, "重复概念", "第二处解释", "source_supported", "second-a", 1)];
  const page = await load(paper([b])); assert.equal(await page.locator("mark.sem").count(), 1);
  await page.locator("mark.sem").click(); const text = await page.locator("#popover").innerText();
  assert.ok(text.includes("第二处解释") && !text.includes("第一处解释"));
  await page.keyboard.press("Escape");
  await page.locator('.card.reading[data-ann="second-a"]').hover();
  assert.ok(await page.locator("mark.sem").evaluate((el) => el.classList.contains("active")));
  await page.dispose();
});

test("model HTML is escaped in highlights, annotations and guide summaries", async () => {
  const b = para("escaped", '正文 <img src=x onerror="window.bad=1"> 的重点。');
  b.highlights = [hl(b, '<img src=x onerror="window.bad=1">', "method")];
  b.annotations = [ann(b, '<img src=x onerror="window.bad=1">', '<script>window.bad=1</script> **说明**')];
  const p = paper([b]); p.reading_guide.summary = '<img src=x onerror="window.bad=1">'; p.reading_guide.summary_refs = [ref(b)];
  const page = await load(p); await page.locator("mark.sem").first().click();
  assert.equal(await page.locator("#popover img, #popover script, .card.reading script").count(), 0);
  await page.keyboard.press("Escape"); await page.locator('[data-act="guide"]').click();
  assert.equal(await page.locator("#guidepanel img").count(), 0);
  assert.equal(await page.evaluate(() => window.bad), undefined);
  await page.dispose();
});

test("highlight opens only its anchored annotation and basis; source button opens the actual PDF block", async () => {
  const b = para("anchors", "第一处概念与第二处方法。", 2);
  b.highlights = [hl(b, "第二处方法", "method", 0, "second")];
  b.annotations = [ann(b, "第一处概念", "无关批注", "author_statement", "first"), ann(b, "第二处方法", "匹配解释", "ai_explanation", "second-ann")];
  const page = await load(paper([b])); await page.locator('mark[data-hl="second"]').click();
  assert.ok((await page.locator("#popover").innerText()).includes("匹配解释"));
  assert.ok((await page.locator("#popover").innerText()).includes("AI 解释"));
  assert.ok(!(await page.locator("#popover").innerText()).includes("无关批注"));
  await page.locator('#popover [data-reading-source]').first().click();
  assert.equal(await page.evaluate(() => PR.side), "pages");
  assert.equal(await page.locator(".pv-label").innerText(), "第 2 / 3 页");
  assert.ok(await page.locator(".pv-hl").evaluate((el) => el.classList.contains("on")));
  await page.dispose();
});

test("Enter/Space activate highlights and Escape restores focus", async () => {
  const b = para("keyboard", "可以按键打开的重点。"); b.highlights = [hl(b, "重点")];
  const page = await load(paper([b])); const mark = page.locator("mark.sem").first();
  assert.equal(await mark.getAttribute("role"), "button");
  for (const key of ["Enter", "Space"]) {
    await mark.focus(); await page.keyboard.press(key);
    assert.ok(await page.locator("#popover").evaluate((el) => el.classList.contains("open")));
    assert.ok(await page.locator("#popover").evaluate((el) => el.contains(document.activeElement)));
    await page.keyboard.press("Escape");
    assert.ok(!(await page.locator("#popover").evaluate((el) => el.classList.contains("open"))));
    assert.ok(await mark.evaluate((el) => el === document.activeElement));
  }
  await page.locator('[data-act="reading"]').focus(); await page.keyboard.press("Enter");
  await page.keyboard.press("Escape");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.act), "reading");
  await page.dispose();
});

test("removing stale reading while its popover is open restores focus to the toolbar", async () => {
  const b = para("focus-update", "更新前的重点。"); b.highlights = [hl(b, "重点")];
  const page = await load(paper([b])); await page.locator("mark.sem").click();
  await page.evaluate(() => { PR.state.paper.reading.pages[1].status = "failed"; PR.applyReadingMarks(); PR.renderMargin(); });
  assert.equal(await page.locator("mark.sem").count(), 0);
  assert.ok(!(await page.locator("#popover").evaluate((el) => el.classList.contains("open"))));
  assert.equal(await page.evaluate(() => document.activeElement.dataset.act), "reading");
  await page.dispose();
});

test("eight category filters, all and off are display-only and persist", async () => {
  const cats = ["background", "related_work", "problem", "method", "innovation", "evidence", "finding", "limitation"];
  const blocks = cats.map((cat, i) => { const b = para("cat" + i, "重点" + i); b.highlights = [hl(b, b.zh, cat)]; return b; });
  const page = await load(paper(blocks)); const before = await page.evaluate(() => JSON.stringify(PR.state.paper));
  for (const cat of cats) {
    await page.locator('[data-act="reading"]').click(); await page.locator(`.rf-row[data-cat="${cat}"]`).click();
    assert.equal(await page.locator("mark.sem").count(), 1);
    assert.equal(await page.locator("mark.sem").getAttribute("data-cat"), cat);
    await page.keyboard.press("Escape");
  }
  await page.locator('[data-act="reading"]').click(); await page.locator('[data-f="all"]').click();
  assert.equal(await page.locator("mark.sem").count(), 8);
  await page.locator('[data-act="reading"]').click(); await page.locator('[data-f="off"]').click();
  assert.equal(await page.locator("mark.sem").count(), 0);
  assert.equal(await page.evaluate(() => JSON.stringify(PR.state.paper)), before);
  await page.reload(); await page.evaluate(() => window.ready);
  assert.equal(await page.locator("mark.sem").count(), 0);
  await page.dispose();
});

test("reader edits invalidate reading immediately and reverting restores it", async () => {
  const b = para("edited", "保留重点的译文。"); b.highlights = [hl(b, "重点")]; b.annotations = [ann(b, "重点", "批注")];
  const page = await load(paper([b]));
  assert.equal(await page.locator("mark.sem").count(), 1);
  await page.evaluate(() => { PR.state.reader.edits.edited = { zh: "已经改过重点的译文。" }; PR.renderBlock("edited"); });
  assert.equal(await page.locator("mark.sem").count(), 0);
  assert.equal(await page.locator(".card.reading").count(), 0);
  await page.evaluate(() => { delete PR.state.reader.edits.edited; PR.renderBlock("edited"); });
  assert.equal(await page.locator("mark.sem").count(), 1);
  assert.equal(await page.locator(".card.reading").count(), 1);
  await page.dispose();
});

test("content hash matches Python for Unicode, original-source precedence, typed table cells and reader edits", async () => {
  const blocks = [para("hash-😀", "**译文😀**\n原始换行"),
    { id: "list-hash", type: "list", page: 1, items: [{ zh: "第一项", en: "First" }, { zh: "第二项", en: "Second😀" }] },
    { id: "math-hash", type: "math", page: 1, tex: "α^2", tag: 0, source_en: "Exact equation" },
    { id: "figure-hash", type: "figure", page: 1, caption_zh: "图注", caption_en: "Original figure caption" },
    { id: "table-hash", type: "table", page: 1, caption_zh: "表注", caption_en: "Rewritten caption", source_en: "Original parsed table",
      head: [["模型😀", "分数"]], rows: [[true, false], [null, ""], [0, -0], [1, 0.95], [1e-7, 1e-6], [1e-5, 1e21]] },
    para("other-page", "另一页不参与指纹", 2)];
  const page = await load(paper(blocks));
  const expected = pythonContentHash(blocks, 1);
  assert.equal(contentHash(blocks, 1), expected, "the fixture must implement the same persisted Python contract");
  assert.equal(await page.evaluate(() => PR.readingContentHash(1)), expected);
  const edited = JSON.parse(JSON.stringify(blocks));
  edited[0].zh = "读者修改后的译文"; edited[1].items[1].zh = "列表修改"; edited[4].caption_zh = "表注修改";
  await page.evaluate(() => {
    PR.state.reader.edits["hash-😀"] = { zh: "读者修改后的译文" };
    PR.state.reader.edits["list-hash#1"] = { zh: "列表修改" };
    PR.state.reader.edits["table-hash#caption"] = { zh: "表注修改" };
  });
  assert.equal(await page.evaluate(() => PR.readingContentHash(1)), pythonContentHash(edited, 1));
  assert.equal(await page.evaluate(() => PR.readingPageValid(1)), false);
  await page.dispose();
});

for (const field of ["math.tex", "math.tag", "table.head", "table.rows", "block.id", "block.type", "block.en", "table.caption_en", "block.source_en"]) {
  test(field + " changes invalidate all reading bound to that page", async () => {
    const b = para("content-bound", "当前公式与实验的重点。");
    b.highlights = [hl(b, "重点")]; b.annotations = [ann(b, "重点", "依赖本页公式或实验值的解释")];
    const math = { id: "eq-content", type: "math", page: 1, tex: "x^2", tag: "1", source_en: "Equation x squared" };
    const table = { id: "tab-content", type: "table", page: 1, num: "1", caption_zh: "实验结果", caption_en: "Results", source_en: "Original results",
      head: [["Method", "Rate"]], rows: [["baseline", 1], ["ours", "0.95"]] };
    if (field === "block.en") delete b.source_en;
    if (field === "table.caption_en") delete table.source_en;
    const page = await load(paper([b, math, table]));
    assert.equal(await page.locator("mark.sem").count(), 1);
    assert.equal(await page.locator(".card.reading").count(), 1);
    await page.evaluate((field) => {
      const [b, math, table] = PR.state.paper.blocks;
      if (field === "math.tex") math.tex = "x^3";
      if (field === "math.tag") math.tag = "2";
      if (field === "table.head") table.head[0][1] = "Error";
      if (field === "table.rows") table.rows[0][1] = 2;
      if (field === "block.id") math.id = "new-equation-id";
      if (field === "block.type") b.type = "heading";
      if (field === "block.en") b.en = "Changed original English";
      if (field === "table.caption_en") table.caption_en = "Changed experiment description";
      if (field === "block.source_en") b.source_en = "Changed saved original source";
      PR.renderPaper(); PR.renderMargin();
    }, field);
    assert.equal(await page.locator("mark.sem").count(), 0);
    assert.equal(await page.locator(".card.reading").count(), 0);
    await page.dispose();
  });
}

test("guide derives real coverage, validates every source and relation, and jumps to that block", async () => {
  const b = para("guide-source", "研究问题的实际出处。", 2); const p = paper([b], 8);
  p.translation.requested_scope = "pages";
  p.reading.insights = [{ id: "problem", type: "problem", content: "实际研究问题", status: "provisional", source_refs: [ref(b)] },
    { id: "method", type: "method", content: "方法待确认", status: "supported", source_refs: [ref(b)] },
    { id: "bad", type: "innovation", content: "错误来源不能显示", source_refs: [{ page: 1, block_id: b.id }] }];
  p.reading_guide.coverage = { total: 2, translated: [1, 2], analyzed: [1, 2, 3], range_complete: true };
  p.reading_guide.summary = "没有来源的总结不显示";
  p.reading_guide.relations = [{ from: "problem", to: "method", content: "无来源关系不显示" },
    { from: "problem", to: "method", content: "有来源的关系", status: "provisional", source_refs: [ref(b)] },
    { from: "missing", to: "method", content: "无端点关系不显示", source_refs: [ref(b)] }];
  const page = await load(p); await page.locator('[data-act="guide"]').click();
  const text = await page.locator("#guidepanel").innerText();
  assert.ok(!text.includes("全文分析"));
  assert.ok(text.includes("1 / 1") && text.includes("8") && text.includes("2"));
  for (const invalid of ["错误来源不能显示", "没有来源的总结不显示", "无来源关系不显示", "无端点关系不显示"]) assert.ok(!text.includes(invalid));
  assert.ok(text.includes("有来源的关系"));
  await page.locator('.gd-item[data-goto="guide-source"]').first().click();
  assert.equal(new URL(page.url()).hash, "#b-guide-source");
  assert.equal(await page.evaluate(() => document.activeElement.id), "b-guide-source");
  await page.dispose();
});

test("full analysis requires every real source page; valid zero-mark pages count", async () => {
  const blocks = [para("p1", "第一页", 1), para("p2", "第二页", 2)];
  const page = await load(paper(blocks, 2)); await page.locator('[data-act="guide"]').click();
  assert.ok((await page.locator("#guidepanel").innerText()).includes("全文分析"));
  await page.evaluate(() => { PR.state.paper.reading.pages[2].status = "failed"; PR.renderPaper(); });
  assert.ok(!(await page.locator("#guidepanel").innerText()).includes("全文分析"));
  assert.ok((await page.locator("#guidepanel").innerText()).includes("1 / 2"));
  await page.dispose();
});

test("newly loaded completed pages render reading immediately; missing sources stay hidden", async () => {
  const b = para("update", "新页面的重点。"); const p = paper([b]); p.reading.pages[1].status = "pending";
  const page = await load(p); assert.equal(await page.locator("mark.sem").count(), 0);
  b.highlights = [hl(b, "重点")]; b.annotations = [ann(b, "重点", "新批注")]; current = state(paper([b]));
  await page.evaluate(async () => { await PR.load(); PR.renderPaper(); PR.renderMargin(); });
  assert.equal(await page.locator("mark.sem").count(), 1);
  assert.equal(await page.locator(".card.reading").count(), 1);
  await page.dispose();
});

test("dark mobile UI fits long annotations and source controls, supports card expansion and guide Escape", async () => {
  const b = para("mobile", "窄屏下的重点。"); b.highlights = [hl(b, "重点")]; b.annotations = [ann(b, "重点", "长批注".repeat(300))];
  const p = paper([b]); p.reading.insights = [{ id: "m", type: "method", content: "方法".repeat(160), source_refs: [ref(b)] }];
  const page = await load(p, { mobile: true }); await page.evaluate(() => PR.applyTheme("dark"));
  await page.locator("mark.sem").click();
  const box = await page.locator("#popover").boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 391 && box.y >= 0 && box.y + box.height <= 845, "popover fits viewport");
  assert.notEqual(await page.locator("mark.sem").evaluate((el) => getComputedStyle(el).backgroundColor), "rgba(0, 0, 0, 0)");
  await page.keyboard.press("Escape");
  await page.locator("#b-mobile .note-pin").click();
  const more = page.locator('.card.reading [data-a="more"]'); assert.equal(await more.count(), 1); await more.click();
  assert.ok(!(await page.locator(".card.reading").evaluate((el) => el.classList.contains("clamp"))));
  await page.locator('[data-act="guide"]').click();
  assert.equal(await page.locator("#guidepanel .side-panel-grip").count(), 1, "render preserves resize grip");
  await page.keyboard.press("Escape");
  assert.equal(await page.evaluate(() => PR.side), null);
  assert.equal(await page.evaluate(() => document.activeElement.dataset.act), "guide");
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no mobile horizontal overflow");
  await page.dispose();
});

test("density thresholds keep unscored candidates and statistics follow category/off filters without requests", async () => {
  const scores = [5, 4, 3, 2, 1, undefined, 0, 6, true, 3.5, "5", null];
  const blocks = scores.map((importance, i) => {
    const b = para("density-" + i, "候选重点" + i);
    b.highlights = [{ ...hl(b, b.zh, i === 0 ? "finding" : "method"), importance }]; return b;
  });
  const invalid = para("invalid-candidate", "有效原文"); invalid.highlights = [hl(invalid, "不存在的引用")];
  const page = await load(paper([...blocks, invalid], 1));
  const before = await page.evaluate(() => JSON.stringify(PR.state.paper));
  assert.equal(await page.locator("mark.sem").count(), 9, "default standard keeps >=4 plus every unscored candidate");
  await assertStats(page, 9, 12, { finding: "1/1", method: "8/11" });
  assert.match(await page.locator("#popover").innerText(), /未评级/);
  for (const [density, count] of [["lite", 8], ["deep", 11], ["standard", 9]]) {
    await setDensity(page, density); assert.equal(await page.locator("mark.sem").count(), count);
    await assertStats(page, count, 12);
  }
  await page.locator('.rf-row[data-cat="finding"]').click();
  assert.equal(await page.locator("mark.sem").count(), 1);
  await assertStats(page, 1, 12, { finding: "1/1", method: "0/11" });
  await page.locator('[data-f="off"]').click();
  assert.equal(await page.locator("mark.sem").count(), 0);
  await assertStats(page, 0, 12, { finding: "0/1", method: "0/11" });
  await page.locator('[data-f="all"]').click();
  assert.equal(await page.locator("mark.sem").count(), 9);
  await assertStats(page, 9, 12);
  assert.equal(await page.evaluate(() => JSON.stringify(PR.state.paper)), before);
  assert.deepEqual(page.uiRequests, [], "display controls must make zero requests");
  assert.deepEqual(requests, []);
  await page.dispose();
});

test("legacy stored off/only filters migrate to standard and quality stays opt-in", async () => {
  const b = para("legacy-filter", "旧标注仍然可见"); b.highlights = [hl(b, b.zh)];
  const page = await load(paper([b]), { filter: { off: true, only: "method" } });
  assert.deepEqual(await page.evaluate(() => PR.readingFilter),
    { off: true, only: "method", density: "standard", quality: false, showSuppressed: false });
  await assertStats(page, 0, 1);
  await page.locator('[data-f="off"]').click();
  await setDensity(page, "lite");
  await page.keyboard.press("Escape"); await page.locator("mark.sem").click();
  const pop = await page.locator("#popover").innerText();
  assert.match(pop, /未评级/); assert.doesNotMatch(pop, /重要性|评分理由|AI评分/);
  await page.reload(); await page.evaluate(() => window.ready);
  assert.equal(await page.evaluate(() => PR.readingFilter.density), "lite");
  assert.equal(await page.locator("mark.sem").count(), 1);
  await page.dispose();
});

test("score and optional reason are escaped, and missing metadata is never invented", async () => {
  const a = para("rated", "有评分的结论"), b = para("reason-only", "仅有理由的局限");
  a.highlights = [{ ...hl(a, a.zh, "finding"), importance: 5, reason: '<img src=x onerror="window.bad=1">核心结果😀' }];
  b.highlights = [{ ...hl(b, b.zh, "limitation"), reason: "作者说明适用条件" }];
  const page = await load(paper([a, b]));
  assert.equal(await page.locator("mark.sem").count(), 2);
  await page.locator('#b-rated mark.sem').click();
  assert.match(await page.locator("#popover").innerText(), /重要性\s*5\s*\/\s*5/);
  assert.match(await page.locator("#popover").innerText(), /核心结果😀/);
  assert.equal(await page.locator("#popover img, #popover script").count(), 0);
  await page.keyboard.press("Escape"); await page.locator('#b-reason-only mark.sem').click();
  assert.match(await page.locator("#popover").innerText(), /作者说明适用条件/);
  assert.doesNotMatch(await page.locator("#popover").innerText(), /重要性/);
  assert.equal(await page.evaluate(() => window.bad), undefined);
  await page.dispose();
});

test("backend quality hash matches browser for emoji, page union, candidate order, unscored values, tables and equations", async () => {
  const a = para("hash😀", "已分析的核心结论😀", 10), b = para("pending😀", "尚未翻译的页", 2);
  a.highlights = [{ ...hl(a, a.zh, "finding"), importance: 5 }];
  b.highlights = [null, "skip non-dict", ...[0, 6, true, 3.5, "4", undefined].map((importance, i) =>
    ({ ...hl(b, b.zh, "method", i), importance }))];
  delete b.highlights[7].occurrence;
  const table = { id: "table😀", type: "table", page: 2, caption_zh: "结果😀", source_en: "Source table",
    head: [["模型", "分数😀"]], rows: [[true, false], [null, 0], [1e-7, 1e21]] };
  const math = { id: "math😀", type: "math", page: 2, tex: "α^2", tag: "1", source_en: "Equation" };
  b.role = "abstract";
  b.highlights[2].source_refs = [{ block_id: b.id, page: 2, source_quote: "Original", nested: { second: "😀", first: [true, null] } }];
  b.highlights[3].source_refs = null;
  const p = paper([a, b, table, math], 10, [10, 3]); p.reading.pages[10].prompt_version = 1;
  const backend = pythonQuality(p); p.reading.quality = backend.quality;
  const page = await load(p);
  assert.equal(await page.evaluate(() => typeof PR.readingQualityInputHash), "function");
  assert.equal(await page.evaluate(() => PR.readingQualityInputHash()), backend.hash);
  for (const field of ["table.rows", "table.head", "math.tex", "math.tag", "candidate.order", "candidate.importance", "candidate.source_refs", "block.role"]) {
    const changed = structuredClone(p);
    if (field === "table.rows") changed.blocks[2].rows[0][1] = "变化😀";
    if (field === "table.head") changed.blocks[2].head[0][1] = "更换指标😀";
    if (field === "math.tex") changed.blocks[3].tex = "α^3";
    if (field === "math.tag") changed.blocks[3].tag = "2";
    if (field === "candidate.order") changed.blocks[1].highlights.reverse();
    if (field === "candidate.importance") changed.blocks[0].highlights[0].importance = 4;
    if (field === "candidate.source_refs") changed.blocks[1].highlights[2].source_refs[0].source_quote = "Changed😀";
    if (field === "block.role") changed.blocks[1].role = "body";
    const expected = pythonQuality(changed).hash;
    assert.notEqual(expected, backend.hash, field + " must change the quality contract");
    await page.evaluate((p) => { PR.state.paper = p; }, changed);
    assert.equal(await page.evaluate(() => PR.readingQualityInputHash()), expected, field);
  }
  await page.evaluate((p) => { PR.state.paper = p; PR.state.reader.edits["hash😀"] = { zh: "读者编辑😀" }; }, p);
  const edited = structuredClone(p); edited.blocks[0].zh = "读者编辑😀";
  assert.equal(await page.evaluate(() => PR.readingQualityInputHash()), pythonQuality(edited).hash);
  assert.deepEqual(page.uiRequests, []);
  await page.dispose();
});

test("canonical hidden by density leaves legacy duplicates visible", async () => {
  const p = duplicatePaper();
  p.blocks[0].highlights[0].importance = 2;
  delete p.blocks[1].highlights[0].importance;
  p.reading.quality = pythonQuality(p).quality;
  const page = await load(p, { filter: { quality: true } });
  try {
    assert.equal(await page.locator('#b-duplicate😀 mark.sem').count(), 1);
    await assertStats(page, 1, 2);
    await setDensity(page, "lite");
    assert.equal(await page.locator('#b-duplicate😀 mark.sem').count(), 1);
    await setDensity(page, "deep");
    assert.equal(await page.locator('#b-canonical😀 mark.sem').count(), 1);
    assert.equal(await page.locator('#b-duplicate😀 mark.sem').count(), 0);
    assert.deepEqual(page.uiRequests, []);
  } finally { await page.dispose(); }
});

test("canonical block_id change invalidates both quality hashes and never hides valid duplicate", async () => {
  const p = duplicatePaper(), oldHash = p.reading.quality.input_hash;
  const page = await load(p, { filter: { quality: true } });
  try {
    assert.equal(await page.locator("mark.sem").count(), 1);
    p.blocks[0].highlights[0].block_id = "wrong-owner";
    assert.notEqual(pythonQuality(p).hash, oldHash);
    await page.evaluate((p) => { PR.state.paper = p; PR.applyReadingMarks(); }, p);
    assert.notEqual(await page.evaluate(() => PR.readingQualityInputHash()), oldHash);
    assert.equal(await page.locator('#b-duplicate😀 mark.sem').count(), 1);
    assert.deepEqual(page.uiRequests, []);
  } finally { await page.dispose(); }
});

test("quality and showSuppressed are reversible display controls with backend keys, counts and persistent preferences", async () => {
  const p = duplicatePaper(), page = await load(p);
  const before = await page.evaluate(() => JSON.stringify(PR.state.paper));
  assert.equal(await page.locator("mark.sem").count(), 2, "quality defaults off before human acceptance");
  await assertStats(page, 2, 2, { finding: "2/2" });
  assert.match(await page.locator("#popover").innerText(), /人工.*未验收|尚未.*人工验收/);
  assert.equal(await page.locator('[data-f="quality"]').count(), 1);
  await page.locator('[data-f="quality"]').click();
  assert.equal(await page.locator("mark.sem").count(), 1);
  assert.equal(await page.locator('#b-canonical😀 mark.sem').count(), 1);
  assert.equal(await page.locator(".card.reading").count(), 1, "quality cannot change annotation validity");
  await assertStats(page, 1, 2, { finding: "1/2" });
  for (const density of ["deep", "standard"]) {
    await setDensity(page, density); assert.equal(await page.locator("mark.sem").count(), 1);
  }
  await page.locator('[data-f="showSuppressed"]').click();
  assert.equal(await page.locator("mark.sem").count(), 2);
  await assertStats(page, 2, 2);
  await setDensity(page, "lite");
  assert.equal(await page.locator("mark.sem").count(), 1, "showSuppressed only bypasses duplicate filtering, not density");
  await setDensity(page, "standard"); await page.locator('[data-f="showSuppressed"]').click();
  assert.equal(await page.locator("mark.sem").count(), 1);
  await page.locator('[data-f="quality"]').click();
  assert.equal(await page.locator("mark.sem").count(), 2);
  await page.locator('[data-f="quality"]').click();
  assert.equal(await page.evaluate(() => JSON.stringify(PR.state.paper)), before);
  assert.deepEqual(page.uiRequests, []); assert.deepEqual(requests, []);
  await page.reload(); await page.evaluate(() => window.ready);
  assert.deepEqual(await page.evaluate(() => PR.readingFilter),
    { off: false, only: "", density: "standard", quality: true, showSuppressed: false });
  assert.equal(await page.locator("mark.sem").count(), 1);
  await page.dispose();
});

for (const change of ["quality.version", "quality.hash", "source.version", "page.status", "page.version", "page.zh_hash",
  "page.content_hash", "page.source_version", "page.prompt_version", "done_pages", "candidate.importance", "candidate.quote", "candidate.order", "candidate.source_refs", "block.role", "reader.edit", "content.table", "content.math"]) {
  test("stale quality after " + change + " cannot suppress another current page", async () => {
    const p = duplicatePaper(), page = await load(p, { filter: { quality: true } });
    assert.equal(await page.locator("mark.sem").count(), 1, "backend fixture must initially suppress the duplicate");
    await page.evaluate((change) => {
      const p = PR.state.paper, st = p.reading.pages[1];
      if (change === "quality.version") p.reading.quality.version = 2;
      if (change === "quality.hash") p.reading.quality.input_hash = "expired";
      if (change === "source.version") p.translation.source_versions[1] = "new-source";
      if (change.startsWith("page.")) st[change.slice(5)] = "changed";
      if (change === "done_pages") p.translation.done_pages = [2, 1];
      if (change === "candidate.importance") p.blocks[0].highlights[0].importance = 4;
      if (change === "candidate.quote") p.blocks[0].highlights[0].quote += "新的候选";
      if (change === "candidate.order") p.blocks.reverse();
      if (change === "candidate.source_refs") p.blocks[0].highlights[0].source_refs = [{ page: 1, block_id: p.blocks[0].id }];
      if (change === "block.role") p.blocks[0].role = "abstract";
      if (change === "reader.edit") PR.state.reader.edits["canonical😀"] = { zh: "读者改过的译文😀" };
      if (change === "content.table") p.blocks.push({ id: "added-table", type: "table", page: 1, head: [["x"]], rows: [[2]] });
      if (change === "content.math") p.blocks.push({ id: "added-equation", type: "math", page: 1, tex: "x^3" });
      PR.renderPaper(); PR.renderMargin();
    }, change);
    assert.equal(await page.locator('#b-duplicate😀 mark.sem').count(), 1, "stale derived data must fail open for current candidates");
    assert.equal(await page.locator(".card.reading").count(), 1);
    await page.dispose();
  });
}

test("finding insights create a nonempty key-conclusions group, preserve limitations and jump to real sources", async () => {
  const b = para("finding-nav", "关键结论的出处", 2), p = paper([b]);
  p.reading.insights = [{ id: "f", type: "finding", content: "模型达到明确的效果", source_refs: [ref(b)] },
    { id: "l", type: "limitation", content: "作者明确的适用限制", source_refs: [ref(b)] }];
  const page = await load(p); await page.locator('[data-act="guide"]').click();
  const text = await page.locator("#guidepanel").innerText();
  assert.match(text, /关键结论/); assert.match(text, /局限性/);
  await page.locator('.gd-group').filter({ hasText: "关键结论" }).locator('.gd-item').click();
  assert.equal(new URL(page.url()).hash, "#b-finding-nav");
  await page.evaluate(() => { PR.state.paper.reading.insights = PR.state.paper.reading.insights.filter((i) => i.type !== "finding"); PR.emit("reading-changed"); });
  assert.doesNotMatch(await page.locator("#guidepanel").innerText(), /关键结论/);
  await page.dispose();
});

test("new colors and density/quality controls fit dark mobile, support keyboard and original-page jumps", async () => {
  const a = para("mobile-finding", "关键结论😀", 2), b = para("mobile-limitation", "作者明确局限", 2);
  a.highlights = [{ ...hl(a, a.zh, "finding"), importance: 5, reason: "评分理由😀".repeat(180) }];
  b.highlights = [{ ...hl(b, b.zh, "limitation"), importance: 4 }];
  const page = await load(paper([a, b]), { mobile: true });
  assert.equal(await page.locator("mark.sem").count(), 2);
  for (const [category, color] of [["finding", "rgb(13, 148, 136)"], ["limitation", "rgb(239, 68, 68)"]]) {
    const mark = page.locator(`mark.sem.cat-${category}`);
    const light = await mark.evaluate((el) => getComputedStyle(el).backgroundColor);
    await openFilter(page);
    assert.equal(await page.locator(`.rf-row[data-cat="${category}"] .dot`).evaluate((el) => getComputedStyle(el).backgroundColor), color);
    await page.keyboard.press("Escape");
    await page.evaluate(() => PR.applyTheme("dark"));
    assert.notEqual(await mark.evaluate((el) => getComputedStyle(el).backgroundColor), light);
    assert.notEqual(await mark.evaluate((el) => getComputedStyle(el).backgroundColor), "rgba(0, 0, 0, 0)");
    await mark.focus(); await page.keyboard.press("Space");
    const box = await page.locator("#popover").boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 391 && box.y >= 0 && box.y + box.height <= 845);
    await page.keyboard.press("Escape"); assert.ok(await mark.evaluate((el) => el === document.activeElement));
    await page.evaluate(() => PR.applyTheme("light"));
  }
  await page.evaluate(() => PR.applyTheme("dark"));
  await openFilter(page);
  for (const selector of ['[data-density="deep"]', '[data-f="quality"]', '[data-f="showSuppressed"]']) {
    assert.equal(await page.locator(selector).count(), 1);
    await page.locator(selector).focus(); await page.keyboard.press("Enter");
    assert.ok(await page.locator(selector).evaluate((el) => el === document.activeElement));
  }
  await page.keyboard.press("Tab"); await page.keyboard.press("Escape");
  await page.locator("mark.sem.cat-finding").focus(); await page.keyboard.press("Enter");
  // The stress case above stays long; the visual acceptance shot uses valid backend metadata.
  await page.keyboard.press("Escape");
  await page.evaluate(() => { PR.state.paper.blocks[0].highlights[0].reason = "作者给出了明确条件下的实验结论，直接回答研究问题。"; });
  await page.locator("mark.sem.cat-finding").click();
  fs.mkdirSync(path.join(__dirname, "shots"), { recursive: true });
  await page.screenshot({ path: path.join(__dirname, "shots", "reading-semantic-mobile-dark.png") });
  await page.locator('#popover [data-reading-source]').click();
  assert.equal(await page.evaluate(() => PR.side), "pages");
  assert.equal(await page.locator(".pv-label").innerText(), "第 2 / 3 页");
  assert.ok(await page.locator(".pv-hl").evaluate((el) => el.classList.contains("on")));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.dispose();
});

test("40-page fixture records actual density/category switch P95 and desktop menu screenshot", async () => {
  const blocks = Array.from({ length: 40 }, (_, i) => {
    const b = para("performance-" + i, "第" + (i + 1) + "页关键结论，方法与证据。", i + 1);
    b.highlights = [{ ...hl(b, "关键结论", "finding", 0, "f-" + i), importance: 5 },
      { ...hl(b, "方法", "method", 0, "m-" + i), importance: 4 },
      { ...hl(b, "证据", "evidence", 0, "e-" + i), importance: 2 }];
    return b;
  });
  const page = await load(paper(blocks, 40));
  assert.equal(await page.locator("mark.sem").count(), 80);
  await openFilter(page);
  assert.equal(await page.locator('[data-density="lite"]').count(), 1);
  const timings = await page.evaluate(async () => {
    const density = [], category = [];
    const sample = async (selector, output) => {
      const start = performance.now(); document.querySelector(selector).click();
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      output.push(performance.now() - start);
    };
    for (let i = 0; i < 30; i++) {
      await sample('[data-density="' + ["lite", "standard", "deep"][i % 3] + '"]', density);
      await sample('.rf-row[data-cat="' + ["finding", "method", "evidence"][i % 3] + '"]', category);
    }
    const p95 = (values) => values.slice().sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1];
    return { densityP95: p95(density), categoryP95: p95(category), samples: density.length };
  });
  console.log(`40-page UI switch P95 (click to two animation frames, ${timings.samples} samples each): density=${timings.densityP95.toFixed(2)}ms category=${timings.categoryP95.toFixed(2)}ms`);
  await page.locator('[data-f="all"]').click(); await setDensity(page, "standard");
  await assertStats(page, 80, 120, { finding: "40/40", method: "40/40", evidence: "0/40" });
  fs.mkdirSync(path.join(__dirname, "shots"), { recursive: true });
  await page.screenshot({ path: path.join(__dirname, "shots", "reading-semantic-desktop-menu.png") });
  assert.deepEqual(page.uiRequests, []); assert.deepEqual(requests, []);
  await page.dispose();
});

// 端到端测试：在临时文献库上跑一遍文献库页和阅读页的主要操作，确认都写进了文件。
// 用法：node tests/e2e.cjs [论文目录（默认 library 里第一篇）]
// 需要：项目 .venv、Node、playwright（找不到时读 PLAYWRIGHT 环境变量）、本机 Chrome 或 Edge。
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn, execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PY = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const pw = process.env.PLAYWRIGHT || 'playwright';
const { chromium } = require(pw);
const BROWSER = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(fs.existsSync);

const LIB = fs.mkdtempSync(path.join(os.tmpdir(), 'easyread-e2e-'));
const SRC = process.argv[2] || fs.readdirSync(path.join(ROOT, 'library')).map((d) => path.join(ROOT, 'library', d)).find((d) => fs.existsSync(path.join(d, 'paper.json')) && JSON.parse(fs.readFileSync(path.join(d, 'paper.json'), 'utf8')).blocks.length > 50);
const PID = path.basename(SRC);
const ROOM = path.join(LIB, PID);
const env = { ...process.env, PYTHONUTF8: '1', EASYREAD_LIBRARY: LIB };
const disk = (name = 'reader') => JSON.parse(fs.readFileSync(path.join(ROOM, name + '.json'), 'utf8'));
const until = async (fn, ms = 3000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (fn()) return true; } catch (e) {} await new Promise((r) => setTimeout(r, 100)); } return false; };
const ok = (cond, msg) => { if (!cond) throw new Error('FAIL: ' + msg); console.log('  ✓ ' + msg); };
const cli = (...args) => execFileSync(PY, ['-m', 'easyread', ...args], { env, cwd: ROOT }).toString();

(async () => {
  fs.cpSync(SRC, ROOM, { recursive: true, filter: (s) => !/history|job\.json|\.lock/.test(s) });
  fs.writeFileSync(path.join(ROOM, 'reader.json'), JSON.stringify({ schema: 2, rev: 0, edits: {}, notes: {}, paper_note: {}, progress: {} }));
  fs.writeFileSync(path.join(ROOM, 'item.json'), JSON.stringify({ added: new Date().toISOString(), tags: [], status: 'unread' }));
  const srv = spawn(PY, ['-m', 'easyread', 'serve', '--port', '0'], { env, cwd: ROOT });
  const url = await new Promise((res, rej) => { srv.stdout.on('data', (d) => { const m = String(d).match(/http:\/\/[\d.:]+/); if (m) res(m[0]); }); setTimeout(() => rej(new Error('服务没起来')), 15000); });
  console.log('server', url, 'library', LIB);
  const browser = await chromium.launch({ headless: true, executablePath: BROWSER });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: url });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(m.text()));
  const saved = () => page.waitForFunction(() => document.querySelector('.save-state').dataset.s === 'saved', null, { timeout: 8000 });
  const shot = (name) => page.screenshot({ path: path.join(__dirname, 'shots', name + '.png') });
  fs.mkdirSync(path.join(__dirname, 'shots'), { recursive: true });

  try {
    console.log('1. 文献库');
    await page.goto(url + '/');
    await page.waitForSelector('.row');
    ok(await page.$$eval('.row', (r) => r.length) === 1, '文献库列出论文');
    await page.fill('#q', '误差条');
    await page.waitForTimeout(250);
    ok(await page.$$eval('.row', (r) => r.length) === 1, '搜索能找到');
    await page.fill('#q', '没有这种论文xyz');
    await page.waitForTimeout(250);
    ok(await page.$$eval('.row', (r) => r.length) === 0, '搜索不到时列表为空');
    await page.fill('#q', '');
    await page.waitForTimeout(250);
    await page.click('.row');
    await page.waitForSelector('#catInput');
    await page.fill('#catInput', '统计方法');
    await page.press('#catInput', 'Enter');
    await page.waitForFunction(() => document.querySelector('.side [data-cat]'));
    ok(await until(() => disk('item').tags.includes('统计方法')), '新建分类并放进论文，写入 item.json');
    await page.click('.side .srow[data-cat="统计方法"]', { button: 'right' });
    await page.click('#ctxmenu button:has-text("置顶")');
    await page.waitForFunction(() => document.querySelector('.side [data-pinrow][data-cat]'));
    ok(true, '分类可以置顶');
    await page.click('[data-status="reading"]');
    await page.waitForTimeout(400);
    ok(disk('item').status === 'reading', '改阅读状态写入 item.json');
    await shot('library');

    console.log('2. 阅读页');
    await page.goto(url + '/read/' + PID);
    await page.waitForSelector('#paper .blk');
    await page.evaluate(() => document.fonts.ready);
    const m = await page.evaluate(() => ({
      blocks: document.querySelectorAll('#paper > .blk').length,
      display: document.querySelectorAll('.blk-math .katex-display').length,
      errs: document.querySelectorAll('.katex-error').length,
      tables: document.querySelectorAll('table.tbl').length,
      barFixed: getComputedStyle(document.querySelector('#bar')).position,
    }));
    ok(m.errs === 0 && m.display > 0, 'KaTeX 公式全部渲染（' + m.display + ' 个行间公式）');
    ok(m.barFixed === 'fixed', '顶栏常驻');
    console.log('2.1 原页面板宽度');
    await page.click('[data-act="pages"]');
    await page.waitForSelector('body.pv-open #pageview');
    const grip = await page.$('#pageview [data-side-grip]');
    ok(!!grip, '原页面板有宽度拖拽手柄');
    const beforePanelWidth = await page.$eval('#pageview', (el) => el.getBoundingClientRect().width);
    const gripBox = await grip.boundingBox();
    await page.mouse.move(gripBox.x + gripBox.width / 2, gripBox.y + 24);
    await page.mouse.down();
    await page.mouse.move(gripBox.x - 120, gripBox.y + 24);
    await page.mouse.up();
    const afterPanelWidth = await page.$eval('#pageview', (el) => el.getBoundingClientRect().width);
    ok(afterPanelWidth > beforePanelWidth + 50, '拖拽可以加宽原页面板');
    ok(Math.abs(await page.evaluate(() => PR.ls.get('easyread-reader-side-w')) - afterPanelWidth) < 2, '面板宽度保存到浏览器');
    await page.click('[data-act="pages"]');
    await page.evaluate(() => scrollTo(0, 3000));
    await page.waitForTimeout(300);
    ok(await page.$eval('#bar', (b) => b.getBoundingClientRect().top) === 0, '往下读顶栏也不收起');

    console.log('3. 字号');
    const fs0 = await page.evaluate(() => PR.prefs.fs);
    await page.keyboard.press('Equal');
    await page.keyboard.press('Equal');
    ok(await page.evaluate(() => PR.prefs.fs) === fs0 + 2, '按两下 = 字号 +2');
    await page.click('[data-act="settings"]');
    await page.$eval('#settings [data-r="fs"]', (r) => { r.value = 22; r.dispatchEvent(new Event('input', { bubbles: true })); });
    ok(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--fs').trim()) === '22px', '滑杆直接拖到 22px');
    await shot('settings');
    await page.keyboard.press('Digit0');
    await page.mouse.click(700, 500);

    console.log('4. 点段落出操作条，写笔记');
    const firstPara = await page.$eval('#paper .blk-para:not(.abstract)', (e) => e.dataset.id);
    await page.evaluate((id) => { document.getElementById('b-' + id).scrollIntoView({ block: 'center' }); }, firstPara);
    await page.click('#b-' + firstPara + ' .zh', { position: { x: 30, y: 10 } });
    await page.waitForSelector('#blockbar.open');
    await shot('blockbar');
    await page.click('#blockbar button[title^="笔记"]');
    await page.waitForSelector('.card.mine textarea');
    await page.keyboard.type('这一段讲的是动机。');
    await page.keyboard.press('Escape');
    await saved();
    ok(Object.values(disk().notes).some((n) => n.anchor === firstPara && n.body.includes('动机')), '段落笔记写入 reader.json');

    console.log('5. 选中文字：彩色划线、提问');
    const selectIn = (id, n) => page.evaluate(([id, n]) => {
      const zh = document.querySelector('#b-' + id + ' .zh');
      const w = document.createTreeWalker(zh, NodeFilter.SHOW_TEXT);
      const t = w.nextNode();
      const r = document.createRange(); r.setStart(t, 0); r.setEnd(t, Math.min(n, t.data.length));
      getSelection().removeAllRanges(); getSelection().addRange(r);
      const rect = r.getBoundingClientRect();
      zh.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: rect.left, clientY: rect.top }));
      return r.toString();
    }, [id, n]);
    const paras = await page.$$eval('#paper .blk-para', (els) => els.slice(2, 6).map((e) => e.dataset.id));
    await page.evaluate((id) => document.getElementById('b-' + id).scrollIntoView({ block: 'center' }), paras[0]);
    const q1 = await selectIn(paras[0], 8);
    await page.waitForSelector('#selbar.open');
    await shot('selbar');
    await page.click('#selbar button[data-color="green"]');
    await saved();
    ok(Object.values(disk().notes).some((n) => n.kind === 'highlight' && n.color === 'green' && n.quote === q1), '绿色划线写入 reader.json');
    ok(await page.$('#b-' + paras[0] + ' mark.hl.c-green'), '划线显示为绿色');
    await page.evaluate((id) => document.getElementById('b-' + id).scrollIntoView({ block: 'center' }), paras[1]);
    await selectIn(paras[1], 6);
    await page.waitForSelector('#selbar.open');
    await page.keyboard.press('q');
    await page.waitForSelector('.card.mine textarea');
    await page.keyboard.type('这里为什么这样说？');
    await page.keyboard.press('Escape');
    await saved();
    const q = Object.values(disk().notes).find((n) => n.kind === 'question');
    ok(q && q.body.includes('为什么'), '选中文字后按 Q 提问，写入 reader.json');

    console.log('6. agent 回复出现在页面');
    ok(cli('status', PID).includes('[待回答]'), 'easyread status 能看到待回答的问题');
    fs.writeFileSync(path.join(LIB, 'reply.json'), JSON.stringify([{ reply_to: q.id, kind: 'reply', body: '因为作者在这里要引出后文的框架。' }]));
    cli('discuss', PID, '--from', path.join(LIB, 'reply.json'));
    await page.waitForSelector('.card.agent[data-anchor="' + paras[1] + '"]', { timeout: 8000 });
    ok(true, '回复几秒内出现在对应段落旁');

    console.log('7. 改译文、译者稿更新不覆盖');
    const editId = paras[2];
    await page.evaluate((id) => document.getElementById('b-' + id).scrollIntoView({ block: 'center' }), editId);
    await page.dblclick('#b-' + editId + ' .zh', { position: { x: 40, y: 12 } });
    await page.waitForSelector('#b-' + editId + ' textarea.editor');
    await page.$eval('#b-' + editId + ' textarea.editor', (t) => { t.value = '【我的译法】' + t.value; t.dispatchEvent(new Event('input', { bubbles: true })); });
    await page.keyboard.press('Control+Enter');
    await saved();
    ok(disk().edits[editId].zh.startsWith('【我的译法】'), '改过的译文写入 reader.json');
    const pj = disk('paper');
    pj.blocks.find((b) => b.id === editId).zh += '（译者修订）';
    fs.writeFileSync(path.join(ROOM, 'paper.json'), JSON.stringify(pj, null, 1));
    await page.waitForSelector('#b-' + editId + ' .stale-tag', { timeout: 8000 });
    ok((await page.$eval('#b-' + editId + ' .zh', (e) => e.textContent)).startsWith('【我的译法】'), '译者稿更新后仍显示我的版本，并提示对比');

    console.log('8. 笔记面板与论文笔记');
    await page.keyboard.press('m');
    await page.waitForSelector('#notespanel .card');
    ok(await page.$$eval('#notespanel .card', (c) => c.length) >= 4, '笔记面板按原文顺序列出批注');
    await page.click('[data-np="paper"]');
    await page.fill('#paperNote', '核心观点：评测是实验，要报误差条。');
    await page.waitForTimeout(900);
    await saved();
    ok((disk().paper_note || {}).body.includes('误差条'), '论文笔记写入 reader.json');
    await shot('notespanel');
    await page.keyboard.press('Escape');

    console.log('9. 术语替换（只算不改）');
    const n = await page.evaluate(() => PR.replaceTerm('标准误差', '标准误', true));
    ok(n > 10, '术语替换能找到 ' + n + ' 处');

    console.log('10. 断线暂存、恢复补存');
    await page.route('**/ops', (route) => route.abort());
    await page.evaluate(() => { const x = Object.values(PR.state.reader.notes).find((z) => z.kind === 'note'); PR.saveNote(Object.assign({}, x, { body: x.body + '（断线时补充）' })); });
    await page.waitForFunction(() => document.querySelector('.save-state').dataset.s === 'offline', null, { timeout: 8000 });
    ok(!JSON.stringify(disk()).includes('断线时补充'), '断线：修改留在浏览器队列');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#paper .blk');
    ok(await page.evaluate(() => PR.store.pending) >= 1, '刷新后待存修改还在');
    await page.unroute('**/ops');
    await page.evaluate(() => PR.flush());
    await saved();
    ok(JSON.stringify(disk()).includes('断线时补充'), '恢复后补存进 reader.json');

    console.log('11. 窄屏与手机');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(500);
    ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '手机宽度下没有横向滚动');
    await shot('mobile');
    ok(errors.length === 0, '页面没有脚本错误' + (errors.length ? '：' + errors.join(' | ') : ''));
    console.log('ALL PASS');
  } catch (e) {
    console.log(String(e.stack || e));
    await shot('fail').catch(() => {});
    if (errors.length) console.log('page errors:', errors);
    process.exitCode = 1;
  } finally {
    await browser.close();
    srv.kill();
    fs.rmSync(LIB, { recursive: true, force: true });
  }
})();

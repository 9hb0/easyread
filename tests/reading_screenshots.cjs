// Real server / real paper smoke test. No model requests or changes to papers.
const fs = require('fs'), path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const out = path.resolve(__dirname, '../artifacts/reading-qa/screenshots');
const url = process.argv[2], pid = process.argv[3] || '03112e8bdaeb';
if (!url) throw new Error('Usage: node tests/reading_screenshots.cjs SERVER_URL [PAPER_ID]');
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(url + '/read/' + pid);
    await page.waitForSelector('#paper .blk');
    console.log('reading diagnostics', await page.evaluate(() => ({
      reading: PR.state.paper.reading,
      sources: PR.state.paper.translation.source_versions,
      valid: Object.keys(PR.state.paper.reading?.pages || {}).map(p => [p, PR.readingPageValid(p)]),
      saved: PR.state.paper.blocks.reduce((n, b) => n + (b.highlights?.length || 0), 0),
      rendered: document.querySelectorAll('mark.sem').length,
      filter: PR.readingFilter,
    })), errors);
    await page.waitForSelector('#paper mark.sem');
    assert.equal(await page.locator('.katex-error').count(), 0);
    const count = await page.locator('mark.sem').count();
    await page.locator('mark.sem').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, pid + '-light.png') });
    await page.locator('mark.sem').first().click();
    await page.waitForSelector('#popover.open');
    await page.screenshot({ path: path.join(out, pid + '-annotation.png') });
    await page.keyboard.press('Escape');
    await page.locator('[data-act=guide]').click();
    await page.waitForSelector('body.gd-open #guidepanel');
    await page.waitForFunction(() => Math.abs(document.querySelector('#guidepanel').getBoundingClientRect().right - innerWidth) < 2);
    await page.screenshot({ path: path.join(out, pid + '-guide.png') });
    const entries = await page.locator('.gd-item[data-goto]').count();
    assert.ok(entries > 0);
    await page.locator('.gd-item[data-goto]').first().click();
    await page.keyboard.press('Escape');
    await page.locator('[data-act=reading]').click();
    await page.waitForSelector('.rf-row');
    await page.locator('[data-f=off]').click();
    assert.equal(await page.locator('mark.sem').count(), 0);
    await page.locator('[data-act=reading]').click();
    await page.locator('[data-f=all]').click();
    assert.equal(await page.locator('mark.sem').count(), count);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
    await page.locator('mark.sem').first().scrollIntoViewIfNeeded();
    await page.locator('mark.sem').first().click();
    await page.screenshot({ path: path.join(out, pid + '-mobile-dark.png') });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(out, pid + '-checks.json'), JSON.stringify({ pid, highlights: count, guide_entries: entries, errors, screenshots: 4 }, null, 2));
    console.log('REAL PAPER BROWSER PASS', pid, 'highlights', count, 'guide entries', entries);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

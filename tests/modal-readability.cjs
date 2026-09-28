// Run with Playwright available: node tests/modal-readability.cjs
// Optional: CHROME_PATH=/path/to/chrome QA_OUTPUT_DIR=/tmp/ti-modal-qa
// Fixtures are served in memory; production intelligence is never rewritten.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const data = JSON.parse(fs.readFileSync(path.join(root, 'data.json'), 'utf8'));
const statuses = ['EARLY_REPORT', 'REPORTED', 'LOCAL_REPORT', 'UNVERIFIED',
  'CORROBORATED', 'CONFIRMED', 'DISPUTED', 'FALSE', 'CORRECTED', 'SUPERSEDED', 'RESOLVED'];
const output = process.env.QA_OUTPUT_DIR || '/tmp/ti-modal-qa';
fs.mkdirSync(output, { recursive: true });

async function checkModal(page, width) {
  const result = await page.locator('.ci-detail-modal').evaluate(modal => {
    const css = getComputedStyle(modal);
    const backdrop = modal.parentElement;
    const rgb = value => value.match(/[\d.]+/g).map(Number);
    const luminance = color => rgb(color).slice(0, 3).map(v => {
      v /= 255;
      return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
    const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) /
      (Math.min(luminance(a), luminance(b)) + .05);
    const text = [...modal.querySelectorAll('h2, p, label, .ci-detail-label, .ci-detail-item span, .ci-detail-source-name, a, .ci-caveat')];
    const rect = modal.getBoundingClientRect();
    return {
      background: css.backgroundColor,
      image: css.backgroundImage,
      opacity: css.opacity,
      shadow: css.boxShadow,
      border: css.borderTopWidth,
      backdropAlpha: rgb(getComputedStyle(backdrop).backgroundColor)[3],
      columns: getComputedStyle(modal.querySelector('.ci-detail-grid')).gridTemplateColumns.split(' ').length,
      fits: rect.left >= 0 && rect.right <= innerWidth && modal.scrollWidth <= modal.clientWidth && backdrop.scrollWidth <= backdrop.clientWidth,
      contrast: Math.min(...text.map(el => contrast(getComputedStyle(el).color,
        el.closest('.ci-detail-item') ? getComputedStyle(el.closest('.ci-detail-item')).backgroundColor : css.backgroundColor)))
    };
  });
  assert.equal(result.background, 'rgb(35, 39, 44)');
  assert.equal(result.image, 'none');
  assert.equal(result.opacity, '1');
  assert.ok(result.backdropAlpha >= .8);
  assert.ok(result.shadow.includes('60px'));
  assert.equal(result.border, '1px');
  assert.equal(result.columns, width <= 700 ? 1 : 2);
  assert.ok(result.fits, 'Modal content must fit horizontally');
  assert.ok(result.contrast >= 4.5, `Text contrast ${result.contrast}`);
  const close = page.locator('.ci-detail-modal').getByRole('button', { name: 'Close', exact: true });
  await close.scrollIntoViewIfNeeded();
  assert.ok(await close.isVisible());
  await close.click();
  assert.equal(await page.locator('.modal-backdrop').count(), 0);
  return result;
}

(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  try {
    const context = await browser.newContext({ serviceWorkers: 'block' });
    let servedData = data;
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://ti.test') return route.abort();
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname === '/data.json') return route.fulfill({ json: servedData });
      const file = path.join(root, url.pathname);
      if (file.startsWith(root + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile())
        return route.fulfill({ path: file });
      return route.fulfill({ status: 404, body: '' });
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const reports = [];
    for (const [width, height] of [[1440, 1000], [700, 900], [390, 844], [320, 568]]) {
      await page.setViewportSize({ width, height });
      servedData = data;
      await page.goto('http://ti.test');
      await page.locator('.ci-card').first().waitFor();
      // Exercise the actual long Iran alert from the reported defect.
      const iran = page.locator('.ci-card').filter({ hasText: 'Operation Economic Outcast' });
      await (await iran.count() ? iran.first() : page.locator('.ci-card').first()).click();
      await page.screenshot({ path: path.join(output, `modal-${width}.png`) });
      reports.push({ width, actual: await checkModal(page, width) });
      for (const status of statuses) {
        const event = { ...Object.values(data.events)[0], eventStatus: status,
          title: `QA ${status}`, publicationStatus: 'PUBLISHED',
          summary: 'Long summary for scrolling. '.repeat(60),
          uncertaintyNotes: 'Uncertainty remains explicit. ' + 'UnbrokenText'.repeat(30) };
        servedData = { ...data, events: [event, ...['DRAFT', 'INTERNAL_REVIEW', 'WITHDRAWN'].map(publicationStatus =>
          ({ ...event, id: `qa-${publicationStatus}`, title: `Hidden ${publicationStatus}`, publicationStatus }))] };
        await page.goto('http://ti.test');
        await page.locator('.ci-card').first().waitFor();
        assert.equal(await page.locator('.ci-card').count(), 1, 'Only published events are visible');
        await page.locator('.ci-card').press('Enter');
        assert.equal(await page.locator('.ci-detail-item').first().locator('span').innerText(), status.replaceAll('_', ' '));
        assert.ok(await page.getByText('Single reporting chain — TI has not independently corroborated this event through a second source.', { exact: true }).last().isVisible());
        await checkModal(page, width);
        await page.locator('.ci-card').click();
        await page.locator('.modal-backdrop').click({ position: { x: 2, y: 2 } });
        assert.equal(await page.locator('.ci-detail-modal').count(), 0);
      }
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(reports, null, 2));
    console.log(`PASS: actual alert + all ${statuses.length} statuses at 4 viewport sizes; publication filtering, contrast, wrapping, scrolling and close actions. Screenshots: ${output}`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

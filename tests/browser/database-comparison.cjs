// Run against the local app; mock catalog reads only, never write production datasets.
// PLAYWRIGHT_MODULE may point at an existing Playwright installation.
const assert = require('node:assert/strict');
require('node:fs').mkdirSync('output/playwright', { recursive: true });
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.CF_BASE_URL || 'http://127.0.0.1:8000';
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CF_CHROMIUM });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const native = [];
    for (let i = 0; i < 5; i++) {
      const response = await page.request.post(`${base}/api/workbench/preview`, {
        data: { family: 'naca65', spec: { family: '65-compressor', thickness_ratio: 0.06 + i * 0.02, design_lift_coefficient: i * 0.3, loading_parameter_a: 1 } }
      });
      assert.equal(response.status(), 200); native.push(await response.json());
    }
    const fixtures = Array.from({ length: 30 }, (_, i) => ({ ...native[i % 5], geometry_id: `${native[i % 5].geometry_id}-fixture-${i}`, name: `测试样本 ${i + 1}`, label: `测试样本 ${i + 1}` }));
    let failFirst = false;
    await page.route('**/api/workbench/profiles**', async route => {
      const url = new URL(route.request().url()), id = url.pathname.split('/')[4];
      if (route.request().method() === 'DELETE') {
        const index = fixtures.findIndex(item => item.geometry_id === decodeURIComponent(id));
        if (index < 0) return route.fulfill({status: 404, json: {detail: 'not found'}});
        fixtures.splice(index, 1); return route.fulfill({json: {deleted: true}});
      }
      if (!id) {
        const offset = Number(url.searchParams.get('offset') || 0), search = url.searchParams.get('search') || '';
        const filtered = fixtures.filter(item => item.name.includes(search) || item.geometry_id.includes(search));
        return route.fulfill({ json: { total: filtered.length, items: filtered.slice(offset, offset + 25).map(item => ({ geometry_id: item.geometry_id, family: item.family, name: item.name, label: item.label })) } });
      }
      if (failFirst && id === fixtures[0].geometry_id) return route.fulfill({ status: 500, json: { detail: '测试读取失败' } });
      const item = fixtures.find(item => item.geometry_id === decodeURIComponent(id));
      return route.fulfill({ status: item ? 200 : 404, json: item || { detail: 'not found' } });
    });
    const waitCharts = async count => {
      await page.waitForFunction(count => {
        const nodes = [...document.querySelectorAll('[data-library-charts] .plotly-chart')];
        return nodes.length === count && nodes.every(node => node._fullLayout)
          && !document.querySelector('[data-profile-export=csv]').disabled;
      }, count);
    };
    await page.goto(`${base}/database`, { waitUntil: 'networkidle' });
    await waitCharts(1);
    assert.equal(await page.locator('[data-library-body] .library-sample').count(), 25);
    assert.equal(await page.locator('[data-library-export-target]').textContent(), `当前导出样本：${fixtures[0].name}`);
    const inputs = page.locator('[data-library-body] input[type=checkbox]');
    for (let i = 0; i < 4; i++) { await inputs.nth(i).check(); await waitCharts(i + 1); }
    await inputs.nth(4).click();
    assert.equal(await inputs.nth(4).isChecked(), false);
    assert.match(await page.locator('[data-library-error]').textContent(), /最多比较 4/);
    assert.equal(await page.locator('.library-chart-card').count(), 4);
    const ranges = await page.locator('[data-library-charts] .plotly-chart').evaluateAll(nodes => nodes.map(node => node._fullLayout.yaxis.range));
    ranges.forEach(range => assert.deepEqual(range, ranges[0]));
    await page.locator('.library-chart-heading button').first().click(); await waitCharts(4);
    assert.match(await page.locator('[data-library-export-target]').textContent(), /测试样本 1/);
    await page.locator('[data-library-next]').click();
    await page.waitForFunction(() => document.querySelector('[data-library-page]').textContent === '第 2 页');
    assert.equal(await page.locator('.library-chart-card').count(), 4);
    await page.locator('[data-library-body] input').first().click();
    assert.equal(await page.locator('[data-library-body] input').first().isChecked(), false);
    await page.locator('[data-library-prev]').click();
    await page.waitForFunction(() => document.querySelector('[data-library-page]').textContent === '第 1 页');
    assert.equal(await page.locator('[data-library-body] input:checked').count(), 4);
    await page.screenshot({ path: 'output/playwright/database-four-compare.png', fullPage: true });
    await inputs.nth(3).uncheck(); await waitCharts(3);
    await page.locator('[data-library-clear]').click(); await waitCharts(1);
    assert.equal(await page.locator('[data-library-body] input:checked').count(), 0);
    await page.locator('.library-sample-button').nth(4).click(); await waitCharts(1);
    assert.match(await page.locator('[data-library-export-target]').textContent(), /测试样本 5/);
    await page.locator('[data-library-filters] input[name=search]').fill('does-not-exist');
    await page.locator('[data-library-filters] button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[data-library-count]').textContent === '共 0 个叶型');
    assert.match(await page.locator('[data-library-body]').textContent(), /没有匹配/);
    await page.locator('[data-library-filters] button[type=reset]').click();
    await page.waitForFunction(() => document.querySelector('[data-library-count]').textContent === '共 30 个叶型');
    await page.setViewportSize({ width: 390, height: 844 });
    await inputs.nth(0).check(); await waitCharts(1); await inputs.nth(1).check(); await waitCharts(2);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
    await page.screenshot({ path: 'output/playwright/database-compare-mobile.png', fullPage: true });
    page.on('dialog', dialog => dialog.accept());
    const deleted = fixtures[1].geometry_id;
    await page.locator('[data-delete-sample]').nth(1).click();
    await page.waitForFunction(() => document.querySelector('[data-library-count]').textContent === '共 29 个叶型');
    await waitCharts(1);
    assert.equal(await page.locator(`[data-sample-id="${deleted}"]`).count(), 0);
    assert.equal(await page.locator(`[data-chart-id="${deleted}"]`).count(), 0);
    assert.match(await page.locator('.library-sample .geometry-id').first().textContent(), /fixture/);
    await page.locator('[data-library-clear]').click(); await waitCharts(1);
    await page.locator('.library-sample-button').nth(4).click(); await waitCharts(1);
    // Check error isolation and retry without a full-page reload.
    failFirst = true;
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('.library-chart-error');
    assert.equal(await page.locator('[data-profile-export=csv]').isDisabled(), true);
    failFirst = false; await page.locator('.library-chart-card button').filter({ hasText: '重试' }).click(); await waitCharts(1);
    assert.equal(await page.locator('[data-profile-export=csv]').isDisabled(), false);
    assert.deepEqual(errors, []);
    console.log('PASS: single preview, 4-image limit, equal axes, active export, cross-page selections, empty search, retry, mobile layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });

// Real native coordinates, mocked DAT catalog: this browser test writes no user files.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.CF_BASE_URL || 'http://127.0.0.1:8000';
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}}), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const native = await (await page.request.post(`${base}/api/workbench/preview`, {data: {family: 'naca65', spec: {family: '65-compressor', thickness_ratio: .1, design_lift_coefficient: .8, loading_parameter_a: 1}}})).json();
    let record = {...native, geometry_id: 'dat_fixture_v1', name: '外部 DAT 样例', family: 'imported', spec: {}, camber: [], suction: native.suction.map(point => point.map(value => value * 5)), pressure: native.pressure.map(point => point.map(value => value * 5))};
    await page.route('**/api/workbench/databases**', async route => {
      if (route.request().url().endsWith('/scan')) {
        record = {...record, geometry_id: 'dat_fixture_v2', name: '已更新 DAT 样例'};
        return route.fulfill({json: {imported: [record.geometry_id], skipped: [], errors: []}});
      }
      return route.fulfill({json: {root: 'test/databases', default: '默认数据库', items: [{name: '默认数据库', path: 'test/databases/默认数据库'}, {name: '空数据库', path: 'test/databases/空数据库'}]}});
    });
    await page.route('**/api/workbench/profiles**', async route => {
      const url = new URL(route.request().url()), id = url.pathname.split('/')[4];
      if (url.pathname.endsWith('/export/dat')) return route.fulfill({body: 'DAT test export', contentType: 'text/plain', headers: {'Content-Disposition': `attachment; filename="${record.geometry_id}.dat"`}});
      if (id) return route.fulfill({json: record});
      const empty = url.searchParams.get('database') === '空数据库';
      return route.fulfill({json: {total: empty ? 0 : 1, items: empty ? [] : [{geometry_id: record.geometry_id, name: record.name, family: record.family}]}});
    });
    await page.goto(`${base}/database`, {waitUntil: 'networkidle'});
    await page.waitForFunction(() => document.querySelector('[data-library-charts] .plotly-chart')?._fullLayout && !document.querySelector('[data-profile-export=dat]').disabled);
    const plot = page.locator('[data-library-charts] .plotly-chart');
    const values = await plot.evaluate(node => ({x: node._fullData[0].x, axis: node._fullLayout.xaxis.title.text}));
    assert.deepEqual(values.x, record.suction.map(point => point[0]));
    assert.match(values.axis, /原坐标/);
    assert.equal(await page.locator('[data-profile-3d]').isDisabled(), true);
    assert.match(await page.locator('[data-coordinate-note]').textContent(), /保留原文件坐标/);
    const pending = page.waitForEvent('download'); await page.locator('[data-profile-export=dat]').click(); assert.equal((await pending).suggestedFilename(), 'dat_fixture_v1.dat');
    await page.locator('[data-library-body] input').first().check();
    await page.locator('[data-database-management] > summary').click();
    await page.locator('[data-database-scan]').click();
    await page.waitForFunction(() => document.querySelector('[data-library-charts] .library-chart-card')?.dataset.chartId === 'dat_fixture_v2' && !document.querySelector('[data-profile-export=dat]').disabled);
    assert.equal(await page.locator('[data-library-body] input:checked').count(), 0);
    await page.locator('[data-database-select]').selectOption('空数据库');
    await page.waitForFunction(() => document.querySelector('[data-library-count]').textContent === '共 0 个叶型');
    assert.equal(await page.locator('.library-chart-card').count(), 0);
    assert.equal(await page.locator('[data-library-empty]').isVisible(), true);
    await page.locator('[data-database-select]').selectOption('默认数据库');
    await page.waitForFunction(() => !document.querySelector('[data-profile-export=dat]').disabled);
    assert.match(await page.locator('[data-library-export-target]').textContent(), /已更新 DAT 样例/);
    assert.deepEqual(errors, []);
    console.log('PASS: raw DAT coordinates, safe 3D disable, coordinate export, scan refresh, database isolation, stale selection cleanup');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exit(1);});

// UI coverage uses real previews and mocked write/list routes; never modifies user datasets.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
fs.mkdirSync('output/playwright', {recursive: true});
const base = process.env.CF_BASE_URL || 'http://127.0.0.1:8000';
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.CF_CHROMIUM});
  try {
    const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    const databases = await (await page.request.get(`${base}/api/workbench/databases`)).json();
    assert.match(databases.root, /databases$/);
    let names = [...databases.items], saved = [], design = null, postedDatabase = null;
    await page.route('**/api/workbench/databases**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/scan')) return route.fulfill({json: {imported: [], skipped: [], errors: []}});
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON(); const item = {name: body.name, path: `${databases.root}/${body.name}`}; names.push(item); return route.fulfill({status: 201, json: item});
      }
      return route.fulfill({json: {...databases, items: names}});
    });
    await page.route('**/api/workbench/profiles**', async route => {
      const url = new URL(route.request().url()), id = url.pathname.split('/')[4];
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON(); postedDatabase = body.database;
        const response = await page.request.post(`${base}/api/workbench/preview`, {data: body});
        const record = await response.json(); saved = [record];
        return route.fulfill({status: 201, json: {geometry_id: record.geometry_id, dataset: body.database}});
      }
      if (id) return route.fulfill({json: saved.find(item => item.geometry_id === id)});
      return route.fulfill({json: {total: saved.length, items: saved.map(item => ({geometry_id: item.geometry_id, family: item.family, name: item.name}))}});
    });
    await page.route('**/api/v2/workspace/designs**', async route => {
      const url = new URL(route.request().url()), id = url.pathname.split('/')[5];
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON(); design = {...body, design_id: 'design_ui_fixture', geometry_id: 'ui_design', revision: 1};
        return route.fulfill({status: 201, json: {design_id: design.design_id, geometry_id: design.geometry_id}});
      }
      if (id) return route.fulfill({json: design});
      return route.fulfill({json: {items: design ? [design] : []}});
    });
    await page.goto(`${base}/studio`, {waitUntil: 'networkidle'});
    await page.waitForFunction(() => document.querySelector('[data-preview-state]').textContent === '已同步');
    assert.equal(await page.locator('textarea[name=explicit_spec]').count(), 0);
    assert.equal(await page.locator('[data-intent-id]').count(), 0);
    assert.equal(await page.locator('[data-geometry-id]').isVisible(), true);
    assert.equal(/geo_v2_[a-f0-9]+/.test(await page.locator('body').innerText()), true);
    assert.match(await page.locator('[data-geometry-label]').textContent(), /βLE.*βTE.*t\/c/);
    assert.equal((await page.locator('body').textContent()).includes('AXIAL–TANGENTIAL PREVIEW'), false);
    assert.equal((await page.locator('body').textContent()).includes('Spec JSON'), false);
    const canvas = await page.locator('#profile-chart').boundingBox(), rail = await page.locator('[data-profile-rail]').boundingBox();
    assert.ok(canvas.height >= 400 && canvas.height <= 430 && rail.x > canvas.x + canvas.width);
    assert.equal(await page.locator('#profile-chart').evaluate(node => node._fullLayout.paper_bgcolor), '#edf4fb');
    assert.equal(await page.locator('#profile-chart').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(237, 244, 251)');
    assert.equal(await page.locator('.toolbar-actions button').count(), 0);
    const primaryLabels = await page.locator('[data-profile-primary-actions] button').allTextContents();
    assert.deepEqual(primaryLabels, ['保存叶型', '导出 CSV', '导出 DAT']);
    assert.equal(await page.locator('[data-save-workspace]').isVisible(), false);
    assert.equal(await page.locator('[data-profile-3d]').isVisible(), false);
    assert.equal(await page.locator('[data-rail-source]').inputValue(), 'dataset');
    await page.locator('[data-database-management] > summary').click();
    await page.locator('[data-database-create]').click();
    await page.locator('[data-database-new-form] input').fill('界面测试数据库');
    await page.locator('[data-database-new-form] button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[data-database-select]').value === '界面测试数据库');
    await page.locator('[data-profile-save]').click();
    await page.waitForFunction(() => document.querySelector('[data-output-status]').textContent.includes('已保存至'));
    assert.equal(postedDatabase, '界面测试数据库');
    assert.match(await page.locator('[data-design-save-state]').textContent(), /已保存至/);
    await page.locator('[data-database-management] > summary').click();
    await page.locator('[data-rail-tab=saved]').click();
    await page.waitForSelector('.rail-saved-card');
    assert.match(await page.locator('[data-rail-list]').textContent(), /自定义叶型/);
    await page.locator('.rail-saved-card button').click();
    await page.waitForFunction(() => document.querySelector('[data-rail-tab=actions]').getAttribute('aria-selected') === 'true');
    await page.screenshot({path: 'output/playwright/generation-custom-clean.png', fullPage: true});
    await page.locator('[data-output-more] > summary').click();
    await page.locator('.rail-design-tools > summary').click();
    await page.locator('[data-save-workspace]').click();
    await page.locator('#workspace-dialog [name=name]').fill('右侧栏设计');
    await page.locator('#workspace-dialog button[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#workspace-dialog').open);
    await page.locator('[data-rail-open-history]').click();
    await page.waitForSelector('.rail-saved-card');
    await page.waitForFunction(() => document.querySelector('[data-rail-list]').textContent.includes('右侧栏设计'));
    assert.match(await page.locator('[data-rail-list]').textContent(), /右侧栏设计/);
    await page.locator('.rail-saved-card button').click();
    await page.waitForFunction(() => document.querySelector('[data-rail-tab=actions]').getAttribute('aria-selected') === 'true');
    await page.waitForFunction(() => !document.querySelector('[data-profile-export=csv]').disabled);
    const pending = page.waitForEvent('download'); await page.locator('[data-profile-export=dat]').click();
    assert.match((await pending).suggestedFilename(), /\.dat$/);
    await page.screenshot({path: 'output/playwright/generation-custom-rail.png', fullPage: true});
    await page.goto(`${base}/standard`, {waitUntil: 'networkidle'});
    await page.locator('[data-standard-form] button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[data-standard-state]').textContent === '已生成');
    assert.deepEqual(await page.locator('[data-profile-primary-actions] button').allTextContents(), primaryLabels);
    assert.equal(await page.locator('[data-profile-3d]').isVisible(), false);
    await page.locator('[data-profile-save]').click();
    await page.waitForFunction(() => document.querySelector('[data-output-status]').textContent.includes('已保存至'));
    assert.equal(postedDatabase, '界面测试数据库');
    await page.locator('[data-rail-tab=saved]').click();
    await page.waitForSelector('.rail-saved-card');
    await page.locator('.rail-saved-card button').click();
    await page.waitForFunction(() => document.querySelector('[data-rail-tab=actions]').getAttribute('aria-selected') === 'true');
    assert.equal(await page.locator('[data-standard-state]').textContent(), '已生成');
    await page.screenshot({path: 'output/playwright/generation-standard-rail.png', fullPage: true});
    await page.locator('[data-rail-tab=actions]').focus(); await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('[data-rail-tab=saved]').getAttribute('aria-selected'), 'true');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await page.locator('[data-rail-tab=actions]').getAttribute('aria-selected'), 'true');
    for (const width of [1280, 1000, 800, 390]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false, `overflow at ${width}`);
    }
    await page.screenshot({path: 'output/playwright/generation-rail-mobile.png', fullPage: true});
    assert.deepEqual(errors, []);
    console.log('PASS: no JSON editor, compact canvas, right-side actions, named save target, custom/standard saved list open, DAT export, keyboard tabs, mobile');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exit(1);});

import { test, expect } from '@playwright/test';
import { base, json, stylesReady } from './live-helpers.js';

test('live observations: every league, five markets, four sources and mobile details are readonly', async ({ page, request }) => {
  test.skip(process.env.FOOTBALL_LIVE_SMOKE !== '1', 'Requires local workbench');
  test.setTimeout(180000);
  const errors = [], writes = [], timings = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') writes.push(r.url()); });
  const initial = await json(request, '/api/v1/observations');
  expect(initial.rows.length).toBeGreaterThan(0);
  for (const source of ['base','market','enhanced','ai']) {
    const list = await json(request, '/api/v1/observations?source='+source+'&node=opening');
    const row = list.rows.find(r => r.observations?.opening?.status === 'completed');
    if (!row) continue;
    const detail = await json(request, '/api/v1/observations/'+encodeURIComponent(row.id));
    const record = detail.observations.find(r => r.node === 'opening' && r.source === source && r.status === 'completed' && (!detail.active_experiment_id || r.experiment_id === detail.active_experiment_id));
    expect(record).toBeTruthy();
    await page.goto(base+'/static/console/index.html#/observations?fixture_id='+encodeURIComponent(row.id));
    await page.getByRole('button',{name:'详情',exact:true}).click();
    await page.getByLabel('详情观察时点').selectOption('opening');
    await page.getByLabel('详情预测来源').selectOption(source);
    await page.getByText('资料与版本',{exact:true}).first().click();
    const model = record.result.model_version || record.result.model_id || record.result.model;
    if (model) await expect(page.getByText(model,{exact:true}).first()).toBeVisible();
    await expect(page.locator('.obs-record').first().locator('.obs-market')).toHaveCount(record.result.market_aware ? 10 : 5);
    await page.keyboard.press('Escape');
    await expect(page.getByText('观察详情',{exact:true})).toBeHidden();
  }
  await page.goto(base + '/static/console/index.html#/observations');
  await expect(page.locator('.obs-list article').first()).toBeVisible({ timeout: 30000 });
  await stylesReady(page);
  for (const cid of ['eng.premier','esp.laliga','ger.bundesliga','ita.serie_a','fra.ligue1']) {
    const started = Date.now();
    const response = page.waitForResponse(r => r.url().includes('/api/v1/observations?') && r.url().includes('competition='+cid));
    await page.getByLabel('观察赛事', {exact:true}).selectOption(cid);
    const body = await (await response).json();
    expect(body.rows.every(r => r.competition_id === cid)).toBeTruthy();
    expect(body.rows.length).toBeGreaterThan(0);
    timings.push({ cid, ms: Date.now()-started, count: body.rows.length });
  }
  for (const [label, values, key] of [['预测来源',['market','enhanced','ai','base'],'source'], ['观察玩法',['hhad','ttg','crs','hafu','had'],'market']]) {
    for (const value of values) {
      const response = page.waitForResponse(r => r.url().includes('/api/v1/observations?') && r.url().includes(key+'='+value));
      await page.getByLabel(label,{exact:true}).selectOption(value);
      expect((await response).ok()).toBeTruthy();
    }
  }
  await page.locator('.obs-list article').first().getByRole('button',{name:'详情',exact:true}).click();
  await expect(page.locator('.obs-detail')).toBeVisible();
  for (const width of [320,390,1440]) {
    await page.setViewportSize({width,height:900});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:390,height:900});
  await page.screenshot({path:'../.ai/tmp/observations-live-390.png',fullPage:true});
  expect(errors).toEqual([]); expect(writes).toEqual([]);
  console.log(JSON.stringify({liveObservationTimings:timings}));
});

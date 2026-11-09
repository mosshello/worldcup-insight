import { test, expect } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { base, json, currentMatches, stylesReady, openDetail } from './live-helpers.js';

const folder = path.resolve('../.ai/tmp/all-pages');
function audit(page) {
  test.skip(process.env.FOOTBALL_LIVE_SMOKE !== '1', 'Requires local workbench');
  test.setTimeout(180000);
  mkdirSync(folder, { recursive: true });
  const errors = [], writes = [], requests = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('requestfailed', r => {
    if (r.url().includes('/assets/') && r.failure()?.errorText !== 'net::ERR_ABORTED')
      errors.push(`${r.url()}: ${r.failure()?.errorText}`);
  });
  page.on('request', r => {
    if (r.url().startsWith(base+'/api/') && !['GET', 'HEAD'].includes(r.method()))
      writes.push({ method: r.method(), url: r.url() });
  });
  page.on('response', async r => {
    if (r.url().startsWith(base+'/api/')) requests.push({ url: r.url(), status: r.status() });
  });
  return { errors, writes, requests };
}
async function capture(page, name) {
  await stylesReady(page);
  await page.screenshot({ path: path.join(folder, name+'-desktop.png'), fullPage: false });
  for (const width of [320, 430, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth),
      { message: name+' must fit '+width+'px' }).toBeLessThanOrEqual(width);
  }
  await stylesReady(page);
  await page.screenshot({ path: path.join(folder, name+'-mobile.png'), fullPage: false });
  await page.setViewportSize({ width: 1440, height: 1000 });
}
async function finish(request, before, evidence, name) {
  expect((await json(request, '/api/v1/state')).state).toEqual(before);
  writeFileSync(path.join(folder, name+'.json'), JSON.stringify(evidence, null, 2));
  expect(evidence.errors).toEqual([]);
  expect(evidence.writes).toEqual([]);
}

test('all main pages: navigation, search, pagination, review drawer and mobile', async ({ page, request }) => {
  const evidence = audit(page), before = (await json(request, '/api/v1/state')).state;
  await page.goto(base+'/static/console/index.html#/matches');
  await expect(page.getByRole('button', { name: '刷新', exact: true })).toBeEnabled({ timeout: 90000 });
  await capture(page, 'matches');
  const search = page.getByPlaceholder('球队 / 场次编号');
  await search.fill('不存在的测试球队XYZ');
  await expect(page.getByText('没有符合条件的已采集比赛。可清除筛选，或等待官方公布场次。', { exact: true })).toBeVisible();
  await search.fill('');
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '实时比分' }).click();
  await expect(page.getByRole('heading', { name: '实时比分', exact: true })).toBeVisible();
  await capture(page, 'scores');
  const nextPage = page.locator('.t-pagination__number').filter({ hasText: /^2$/ });
  if (await nextPage.count()) {
    await nextPage.click();
    await expect(nextPage).toHaveClass(/current/);
  }
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '复盘记录' }).click();
  await expect(page.locator('.review-summary strong').first()).not.toHaveText('0', { timeout: 90000 });
  await capture(page, 'reviews');
  await page.getByPlaceholder('搜索球队').fill('不存在的测试球队XYZ');
  await expect(page.getByText('暂无已完场比赛或赛前预测归档，后台正在同步赛果。', { exact: true })).toBeVisible();
  await page.getByPlaceholder('搜索球队').fill('');
  await page.getByRole('button', { name: '查看复盘', exact: true }).first().click();
  await expect(page.locator('.t-drawer--open')).toBeVisible();
  await capture(page, 'review-drawer');
  await page.locator('.t-drawer--open').getByRole('button', { name: '关闭', exact: true }).click();
  await finish(request, before, evidence, 'main-pages');
});

test('all four match tabs and both odds sources render on desktop and mobile', async ({ page, request }) => {
  const evidence = audit(page), before = (await json(request, '/api/v1/state')).state;
  const matches = await currentMatches(request);
  const match = matches.find(m => Date.parse(m.kickoff) > Date.now()) || matches[0];
  await openDetail(page, match, 'basic');
  const tabs = [['基本面','basic'],['阵容情报','lineups'],['赔率','odds'],['预测分析','analysis']];
  for (const [label, key] of tabs) {
    await page.getByText(label, { exact: true }).first().click();
    await expect(page).toHaveURL(new RegExp('/'+key+'$'));
    await capture(page, 'match-'+key);
    if (key === 'odds') {
      await page.getByText('外盘参考', { exact: true }).click();
      await capture(page, 'match-external-odds');
      await page.getByText('体彩 SP', { exact: true }).click();
    }
  }
  await page.getByRole('link', { name: '查看关联复盘', exact: true }).click();
  const archived = (await json(request, '/api/v1/state')).state.versions[match.id];
  if (archived?.length) await expect(page.locator('.t-drawer--open')).toBeVisible({ timeout: 90000 });
  await finish(request, before, evidence, 'match-pages');
});

test('all six model workspace tabs, drawers and task form are usable with real data', async ({ page, request }) => {
  const evidence = audit(page), before = (await json(request, '/api/v1/state')).state;
  await page.addInitScript(() => localStorage.setItem('football:model-competition','uefa.ucl'));
  await page.goto(base+'/static/console/index.html#/models');
  await expect(page.getByRole('heading', { name: '比赛资格', exact: true })).toBeVisible({ timeout: 90000 });
  const tabs = [['赛事概览','overview'],['球队与参赛','teams'],['数据接入','sources'],
    ['数据集与质量','datasets'],['训练与评估','training'],['模型与使用','models']];
  for (const [label, key] of tabs) {
    await page.locator('.t-tabs__nav-item').filter({ hasText: label }).click();
    await capture(page, 'models-'+key);
    const details = page.getByRole('button', { name: '详情', exact: true });
    if (await details.count()) {
      await details.first().click();
      await expect(page.locator('.t-drawer--open')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.locator('.t-drawer--open')).toHaveCount(0);
    }
    if (key === 'training') {
      await page.getByRole('button', { name: '新建任务', exact: true }).click();
      await expect(page.getByRole('button', { name: '创建任务', exact: true })).toBeVisible();
      await capture(page, 'models-new-task');
      await page.getByRole('button', { name: '取消', exact: true }).click();
    }
    if (key === 'models') {
      await page.getByRole('button', { name: '查看报告', exact: true }).click();
      await expect(page.locator('.t-drawer--open')).toBeVisible({ timeout: 90000 });
      await capture(page, 'models-report');
      await page.keyboard.press('Escape');
    }
  }
  await finish(request, before, evidence, 'model-pages');
});

test('every connected competition and an unconfigured competition render correct scoped workspaces', async ({ page, request }) => {
  const evidence = audit(page), before = (await json(request, '/api/v1/state')).state;
  await page.addInitScript(() => localStorage.setItem('football:model-competition','uefa.ucl'));
  await page.goto(base+'/static/console/index.html#/models');
  await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeEnabled({ timeout: 90000 });
  for (const [id, label] of [['jp.j1','日职'],['kr.k1','韩职'],['afc.champions','亚冠精英'],
    ['uefa.ucl','欧冠'],['uefa.uel','欧联'],['eng.championship','英冠'],['jp.emperors_cup','天皇杯']]) {
    await page.locator('.competition-selector input').click();
    const waiting = page.waitForResponse(r => r.url().includes('/api/v2/workspaces?competition_id='+id));
    await page.locator('.t-select-option').filter({ hasText: new RegExp('^'+label+'$') }).click();
    const response = await waiting, result = await response.json();
    expect(response.ok()).toBe(true);
    expect(result.workspace.competition.id).toBe(id);
    await expect(page.locator('.competition-selector input')).toHaveValue(label);
    await expect(page.getByRole('heading', { name: '比赛资格', exact: true })).toBeVisible();
    await stylesReady(page);
    if (id === 'jp.emperors_cup') {
      await page.getByText('训练与评估', { exact: true }).click();
      await expect(page.getByRole('button', { name: '新建任务', exact: true })).toBeDisabled();
    }
  }
  await finish(request, before, evidence, 'competition-switches');
});

test('repeated full page navigation keeps all styles and scripts available', async ({ page, request }) => {
  const evidence = audit(page), before = (await json(request, '/api/v1/state')).state;
  for (const route of ['matches','scores','reviews','daily-sweep','models','observations','matches','models']) {
    await page.goto(base+'/static/console/index.html?asset-check='+Date.now()+'#/'+route);
    await stylesReady(page);
  }
  await finish(request, before, evidence, 'asset-navigation');
});

import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

test("local automatic briefing, external prices and process-linked review preserve archives", async ({ page, request }) => {
  test.skip(process.env.FOOTBALL_LIVE_SMOKE !== "1", "Requires the running local workbench");
  test.setTimeout(120000);
  const base = "http://127.0.0.1:8891";
  const artifacts = path.resolve("../.ai/tmp");
  mkdirSync(artifacts, { recursive: true });
  const original = (await (await request.get(base + '/api/v1/state')).json()).state.versions;
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const future = (await (await request.get(base + '/api/v1/matches/sporttery%3A2042499?refresh=1', {timeout:60000})).json()).detail;
  expect(future.briefing.findings.length).toBeGreaterThan(2);
  expect(future.context.external_odds.rows.length).toBeGreaterThan(0);
  expect(future.briefing.is_archived).toBe(false);
  await page.setViewportSize({width:1500,height:1000});
  await page.goto(base + '/console#/match/sporttery:2042499/analysis');
  await expect(page.getByRole('heading',{name:'赛前自动研判',exact:true})).toBeVisible({timeout:20000});
  await expect(page.getByLabel('自动比赛研判')).toContainText('当前市场定价');
  await expect(page.getByLabel('自动比赛研判')).toContainText('伤停与资格');
  await page.screenshot({path:path.join(artifacts,'complete-auto-analysis.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.setViewportSize({width:1500,height:1000});
  await page.goto(base + '/console#/match/sporttery:2042499/odds');
  await page.getByText('外盘参考',{exact:true}).click();
  await expect(page.getByText('500公开欧赔',{exact:true})).toBeVisible();
  await page.screenshot({path:path.join(artifacts,'complete-external-odds.png'),fullPage:true});
  const completed = (await (await request.get(base + '/api/v1/matches/sporttery%3A2042443?refresh=1', {timeout:60000})).json()).detail;
  expect(completed.context.match_facts.statistics.length).toBeGreaterThan(0);
  expect(completed.context.match_facts.events.some(e=>e.type==='red-card')).toBe(true);
  await page.goto(base + '/console#/reviews/sporttery:2042443');
  await expect(page.locator('.t-drawer').getByLabel('比赛过程资料')).toBeVisible();
  await expect(page.locator('.t-drawer')).toContainText('射正');
  await page.screenshot({path:path.join(artifacts,'complete-process-review.png'),fullPage:true});
  const after = (await (await request.get(base + '/api/v1/state')).json()).state.versions;
  expect(after).toEqual(original);
  expect(errors).toEqual([]);
});

test("local Asian fundamentals, competition schedules and sourced personnel", async ({ page, request }) => {
  test.skip(process.env.FOOTBALL_LIVE_SMOKE !== "1", "Requires the running local workbench");
  test.setTimeout(90000);
  const base = "http://127.0.0.1:8891";
  const artifacts = path.resolve("../.ai/tmp");
  mkdirSync(artifacts, { recursive: true });
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  for (const id of ["2042495", "2042499"]) {
    const response = await request.get(`${base}/api/v1/matches/sporttery%3A${id}?refresh=1`, { timeout: 60000 });
    expect(response.ok()).toBeTruthy();
    const { detail } = await response.json();
    for (const rows of detail.context.recent) {
      expect(rows).toHaveLength(5);
      expect(rows.every(r => r.league && r.competition_type)).toBeTruthy();
    }
    for (const rows of detail.context.upcoming) expect(rows.length).toBeGreaterThan(0);
    expect(detail.context.estimated_lineups).toHaveLength(2);
    expect(detail.context.estimated_lineups.every(r => r.status === "estimated" && r.players.length === 11)).toBeTruthy();
    if (id === "2042499") {
      expect(detail.context.injuries.filter(r => r.official)).toHaveLength(7);
      expect(detail.context.estimated_lineups.find(r => r.side === "away").conflicts.length).toBeGreaterThan(0);
      expect(detail.context.injuries_verified).toBe(false);
    }
  }
  await page.setViewportSize({ width: 1500, height: 1000 });
  await page.goto(`${base}/console#/match/sporttery:2042495/basic`);
  await expect(page.getByRole("heading", { name: "上海海港 · 后续赛程", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "中超 · 联赛", exact: true }).first()).toBeVisible();
  await page.screenshot({ path: path.join(artifacts, "asian-basic-enriched.png"), fullPage: true });
  await page.goto(`${base}/console#/match/sporttery:2042499/basic`);
  await expect(page.getByRole("cell", { name: "德国杯 · 国内杯赛", exact: true }).first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "欧冠 · 洲际杯赛", exact: true }).first()).toBeVisible();
  await page.goto(`${base}/console#/match/sporttery:2042499/lineups`);
  await expect(page.getByText("预计阵容 · 非正式首发", { exact: true })).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "伤停与出场状态", exact: true })).toBeVisible();
  await expect(page.getByText(/来源冲突：/)).toBeVisible();
  await expect(page.getByText("官方公告为人工核验摘录，仅适用本场；不是实时伤停接口。", { exact: true })).toBeVisible();
  const personnelTable = page.locator("section").filter({ has: page.getByRole("heading", { name: "伤停与出场状态", exact: true }) }).locator(".t-table");
  expect(await personnelTable.locator("tbody tr").evaluateAll(rows => rows.every(row => row.getBoundingClientRect().height < 70))).toBe(true);
  await page.screenshot({ path: path.join(artifacts, "ucl-personnel-enriched.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("local Chinese opponents and legacy-linked lineup analysis", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.FOOTBALL_LIVE_SMOKE !== "1",
    "Requires the running local workbench",
  );
  const base = "http://127.0.0.1:8891";
  const artifacts = path.resolve("../.ai/tmp");
  mkdirSync(artifacts, { recursive: true });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const response = await request.get(
    base + "/api/v1/matches/sporttery%3A2042498?refresh=1",
  );
  const { detail } = await response.json();
  expect(detail.context.recent[0].map((r) => r.opponent)).toContain(
    "科尼亚体育",
  );
  expect(detail.context.recent[1].map((r) => r.opponent)).toContain("勒沃库森");
  await page.setViewportSize({ width: 1500, height: 1000 });
  await page.goto(base + "/console#/match/sporttery:2042498/basic");
  await expect(
    page.getByRole("cell", { name: "科尼亚体育", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "勒沃库森", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(artifacts, "chinese-opponents-live.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.goto(base + "/console#/match/sporttery:2042443/analysis");
  await expect(page.getByLabel("旧版预测记录")).toContainText("主胜");
  await expect(page.locator(".intelligence-panel")).toContainText(
    "首发11人已确认",
  );
  await expect(page.locator(".intelligence-panel")).toContainText("1.95");
  await page.screenshot({
    path: path.join(artifacts, "analysis-006-live.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("link", { name: "查看关联复盘", exact: true }).click();
  await expect(page.locator(".t-drawer")).toContainText("纽卡斯尔");
  await expect(page.locator(".t-drawer")).toContainText("旧版预测记录");
  await page.screenshot({
    path: path.join(artifacts, "review-006-linked-live.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(
    () => page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

// Opt-in read-only acceptance against the actual local service, never fixture data.
test("local finished matches render factual reports without pregame archives", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.FOOTBALL_LIVE_SMOKE !== "1",
    "Requires the running local workbench",
  );
  const base = "http://127.0.0.1:8891";
  const response = await request.get(base + "/api/v1/reviews");
  expect(response.ok()).toBeTruthy();
  const { reviews } = await response.json();
  const factual = reviews.filter(
    (r) => !r.prediction && r.postmatch_review?.id,
  );
  expect(factual.length).toBeGreaterThan(0);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1600, height: 1050 });
  await page.goto(base + "/console#/reviews");
  await expect(page.locator(".review-summary > div").first()).toContainText(
    String(reviews.length),
  );
  await expect(
    page.getByRole("button", { name: "查看复盘", exact: true }),
  ).toHaveCount(Math.min(reviews.length, 10));
  const artifacts = path.resolve("../.ai/tmp");
  mkdirSync(artifacts, { recursive: true });
  await page.screenshot({
    path: path.join(artifacts, "reviews_live_fixed_v2.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "查看复盘", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "赛后事实", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".t-drawer")).toContainText("本场没有赛前预测归档");
  await page.screenshot({
    path: path.join(artifacts, "review_fact_live_v2.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 850, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

import { test, expect } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  base,
  json,
  currentMatches,
  openDetail,
  openReviews,
  assertGate,
} from "./live-helpers.js";
const artifacts = path.resolve("../.ai/tmp");
function live() {
  test.skip(
    process.env.FOOTBALL_LIVE_SMOKE !== "1",
    "Requires the running local workbench",
  );
  test.setTimeout(180000);
  mkdirSync(artifacts, { recursive: true });
}
function errorsFor(page) {
  const errors = [];
  page.liveAssetEvidence = [];
  page.on("response", (r) => {
    if (r.request().resourceType() === "stylesheet")
      page.liveAssetEvidence.push({
        url: r.url(),
        status: r.status(),
        type: r.headers()["content-type"],
      });
  });
  page.on("requestfailed", (r) =>
    page.liveAssetEvidence.push({ url: r.url(), failure: r.failure() }),
  );
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}
test.afterEach(async ({ page }, info) => {
  if (info.status !== info.expectedStatus) {
    const evidence = {
      url: page.url(),
      network: page.liveAssetEvidence || [],
      styles: await page
        .locator('link[rel="stylesheet"]')
        .evaluateAll((links) =>
          links.map((link) => ({ href: link.href, loaded: !!link.sheet })),
        ),
    };
    await info.attach("live-asset-evidence", {
      body: JSON.stringify(evidence, null, 2),
      contentType: "application/json",
    });
    writeFileSync(
      path.join(artifacts, "live-asset-failure.json"),
      JSON.stringify(evidence, null, 2),
    );
  }
});
async function unchanged(request, before) {
  expect((await json(request, "/api/v1/state")).state.versions).toEqual(before);
}

test("local discovered analysis and actual external coverage preserve archives", async ({
  page,
  request,
}) => {
  live();
  const errors = errorsFor(page);
  const before = (await json(request, "/api/v1/state")).state.versions;
  const matches = await currentMatches(request);
  const match =
    matches.find((m) => Date.parse(m.kickoff) > Date.now()) || matches[0];
  const detail = await openDetail(page, match);
  expect(detail.briefing).toBeTruthy();
  expect(detail.briefing.is_archived).toBe(false);
  await expect(page.getByLabel("自动比赛研判")).toContainText(
    detail.briefing.title,
  );
  for (const finding of (detail.briefing.findings || []).slice(0, 2))
    await expect(page.getByLabel("自动比赛研判")).toContainText(finding.title);
  await assertGate(page, detail);
  await page.screenshot({
    path: path.join(artifacts, "current-auto-analysis.png"),
    fullPage: true,
  });
  const odds = await openDetail(page, match, "odds");
  await page.getByText("外盘参考", { exact: true }).click();
  if (odds.context?.external_odds?.rows?.length)
    await expect(
      page.getByText(odds.context.external_odds.source, { exact: true }),
    ).toBeVisible();
  else
    await expect(
      page.getByRole("heading", { name: "暂无已匹配的外盘数据", exact: true }),
    ).toBeVisible();
  writeFileSync(
    path.join(artifacts, "live-smoke-coverage.json"),
    JSON.stringify(
      {
        match_id: match.id,
        external_rows: odds.context?.external_odds?.rows?.length || 0,
        model_inputs: detail.quality?.model_inputs,
      },
      null,
      2,
    ),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await unchanged(request, before);
  expect(errors).toEqual([]);
});

test("local discovered fundamentals and personnel agree with source coverage", async ({
  page,
  request,
}) => {
  live();
  const errors = errorsFor(page);
  const before = (await json(request, "/api/v1/state")).state.versions;
  const matches = await currentMatches(request);
  const match =
    matches.find((m) => Date.parse(m.kickoff) < Date.now()) || matches[0];
  const detail = await openDetail(page, match, "basic");
  await expect(
    page.getByRole("heading", { name: "球队对比", exact: true }),
  ).toBeVisible();
  const recent = (detail.context?.recent || []).flat();
  for (const row of recent.slice(0, 3)) {
    expect(row.opponent).toBeTruthy();
    await expect(
      page.getByRole("cell", { name: row.opponent, exact: true }).first(),
    ).toBeVisible();
  }
  const lineups = await openDetail(page, match, "lineups");
  await expect(
    page.getByRole("heading", { name: "伤停与出场状态", exact: true }),
  ).toBeVisible();
  const names = (lineups.context?.injuries || [])
    .map((row) => row.player || row.name)
    .filter(Boolean);
  for (const name of names.slice(0, 2))
    await expect(
      page.getByRole("cell", { name, exact: true }).first(),
    ).toBeVisible();
  if (!names.length)
    await expect(
      page.getByText("来源暂未给出可核实伤停名单，不代表无人缺席", {
        exact: true,
      }),
    ).toBeVisible();
  for (const row of lineups.context?.lineups || [])
    if (row.status === "confirmed")
      expect(
        new Set(
          row.players.map((p) => (typeof p === "string" ? p : p.id || p.name)),
        ).size,
      ).toBe(11);
  const personnel = page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "伤停与出场状态", exact: true }),
    })
    .locator(".t-table");
  const wrappedCells = await personnel
    .locator("tbody td:not([colspan])")
    .evaluateAll((cells) =>
      cells
        .filter((cell) => getComputedStyle(cell).whiteSpace !== "nowrap")
        .map((cell) => ({
          text: cell.innerText,
          whiteSpace: getComputedStyle(cell).whiteSpace,
          className: cell.className,
          rowHeight: cell.parentElement.getBoundingClientRect().height,
        })),
    );
  expect(
    wrappedCells,
    "Every personnel data cell must remain single-line",
  ).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await unchanged(request, before);
  expect(errors).toEqual([]);
});

test("local legacy-linked reviews preserve original direction and archives", async ({
  page,
  request,
}) => {
  live();
  const errors = errorsFor(page);
  const before = (await json(request, "/api/v1/state")).state.versions;
  const records = await json(request, "/api/v1/reviews");
  const legacy = records.reviews.find((row) => row.legacy_prediction);
  expect(
    legacy,
    "Actual review records must contain the preserved legacy archive",
  ).toBeTruthy();
  await openReviews(page, legacy.id);
  const drawer = page.locator(".t-drawer--open");
  await expect(drawer.getByLabel("旧版预测记录")).toContainText(
    legacy.legacy_prediction.direction,
  );
  await expect(drawer).toContainText(legacy.home);
  await expect(drawer).toContainText(legacy.away);
  await page.screenshot({
    path: path.join(artifacts, "current-legacy-review.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await unchanged(request, before);
  expect(errors).toEqual([]);
});

test("local discovered finished matches render factual reports without pregame archives", async ({
  page,
  request,
}) => {
  live();
  const errors = errorsFor(page);
  const before = (await json(request, "/api/v1/state")).state.versions;
  const records = await json(request, "/api/v1/reviews");
  const factual = records.reviews.find(
    (row) => !row.prediction && row.postmatch_review?.id,
  );
  expect(
    factual,
    "Actual source-confirmed results must have a factual report",
  ).toBeTruthy();
  const loaded = await openReviews(page, factual.id);
  const shown = loaded.find((row) => row.id === factual.id);
  expect(shown.prediction).toBeFalsy();
  const drawer = page.locator(".t-drawer--open");
  await expect(drawer).toContainText(shown.postmatch_review.summary);
  await expect(
    drawer.getByRole("heading", { name: "赛后事实", exact: true }),
  ).toBeVisible();
  await expect(drawer).toContainText("本场未归档赛前预测");
  if (shown.match_facts?.statistics?.length)
    await expect(drawer.getByLabel("比赛过程资料")).toBeVisible();
  await page.screenshot({
    path: path.join(artifacts, "current-factual-review.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 850, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await unchanged(request, before);
  expect(errors).toEqual([]);
});

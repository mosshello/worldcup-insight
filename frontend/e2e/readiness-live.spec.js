import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import {
  json,
  currentMatches,
  openDetail,
  assertGate,
} from "./live-helpers.js";
test("current local fixtures reflect model readiness without rewriting archives", async ({
  page,
  request,
}) => {
  test.skip(process.env.FOOTBALL_LIVE_SMOKE !== "1", "Requires local service");
  test.setTimeout(180000);
  const before = (await json(request, "/api/v1/state")).state.versions;
  const matches = await currentMatches(request);
  const future = matches.filter((m) => Date.parse(m.kickoff) > Date.now());
  test.info().annotations.push({type:'live-coverage',description:`Upcoming fixtures: ${future.length}; collected fixtures: ${matches.length}`});
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const detail = await openDetail(page, future[0] || matches[0]);
  expect(detail.quality?.model_inputs?.policy).toBe("model-inputs-v2");
  expect(detail.quality.can_predict).toBe(
    detail.quality.model_inputs.can_predict,
  );
  await assertGate(page, detail);
  if (
    detail.quality.model_inputs.checks.some(
      (check) => check.status === "blocked",
    )
  )
    expect(detail.quality.can_predict).toBe(false);
  expect(
    await page.locator('script[type="module"]').getAttribute("src"),
  ).toMatch(/index-.*\.js$/);
  const folder = path.resolve("../.ai/tmp");
  mkdirSync(folder, { recursive: true });
  await page.screenshot({
    path: path.join(folder, "readiness-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(folder, "readiness-mobile.png"),
    fullPage: true,
  });
  expect((await json(request, "/api/v1/state")).state.versions).toEqual(before);
  expect(errors).toEqual([]);
});

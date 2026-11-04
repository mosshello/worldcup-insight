import { expect } from "@playwright/test";
export const base = "http://127.0.0.1:8891";
export async function stylesReady(page) {
  await expect
    .poll(
      () =>
        page
          .locator('link[rel="stylesheet"]')
          .evaluateAll(
            (links) => links.length > 0 && links.every((link) => link.sheet?.cssRules.length > 0),
          ),
      {
        timeout: 15000,
        message:
          "Published stylesheets must be applied before visual assertions",
      },
    )
    .toBe(true);
  await expect(page.locator('.app-header')).toHaveCSS('display', 'flex');
}
export async function json(request, url) {
  const r = await request.get(base + url, { timeout: 90000 });
  expect(r.ok(), `${url}: HTTP ${r.status()}`).toBe(true);
  return r.json();
}
export async function currentMatches(request) {
  const result = await json(request, "/api/v1/matches");
  expect(result.matches.length).toBeGreaterThan(0);
  return result.matches;
}
export async function openDetail(page, match, tab = "analysis") {
  const pending = page.waitForResponse(
    (r) =>
      decodeURIComponent(new URL(r.url()).pathname) ===
      "/api/v1/matches/" + match.id,
    { timeout: 90000 },
  );
  await page.goto(
    base +
      "/static/console/index.html?smoke=" +
      Date.now() +
      "#/match/" +
      encodeURIComponent(match.id) +
      "/" +
      tab,
  );
  const response = await pending;
  expect(response.ok()).toBe(true);
  const result = await response.json();
  expect(result.detail).toBeTruthy();
  await stylesReady(page);
  await expect(
    page.getByRole("button", { name: "刷新数据", exact: true }),
  ).toBeEnabled({ timeout: 90000 });
  await expect(
    page.getByRole("heading", { name: "正在核对比赛与采集数据…", exact: true }),
  ).toHaveCount(0);
  return result.detail;
}
export async function openReviews(page, id) {
  const pending = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/reviews",
    { timeout: 90000 },
  );
  await page.goto(
    base +
      "/static/console/index.html?smoke=" +
      Date.now() +
      "#/reviews" +
      (id ? "/" + encodeURIComponent(id) : ""),
  );
  const response = await pending;
  expect(response.ok()).toBe(true);
  const result = await response.json();
  await stylesReady(page);
  await expect(page.locator(".review-summary > div").first()).toContainText(
    String(result.reviews.length),
    { timeout: 90000 },
  );
  return result.reviews;
}
export async function assertGate(page, detail) {
  const button = page.getByRole("button", { name: /^(生成|更新)预测$/ });
  if (detail.quality?.can_predict !== true) {
    await expect(button).toBeDisabled();
    const reasons = detail.quality?.reasons || [];
    expect(reasons.length).toBeGreaterThan(0);
    await expect(page.locator('.inline-notice[role="alert"]')).toContainText(
      reasons[0],
    );
  } else await expect(button).toBeEnabled();
}

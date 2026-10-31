import { test, expect } from "@playwright/test";
test.beforeEach(async ({ page }) => {
  await page.goto("/club-preview.html");
  await expect(
    page.getByRole("heading", { name: "比赛列表", exact: true }),
  ).toBeVisible();
});
test("match filters, favorites, detail tabs and persisted notes", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.getByPlaceholder("球队 / 场次编号").fill("阿森纳");
  await expect(page.locator(".desktop-matches tbody tr")).toHaveCount(1);
  await page
    .locator(".desktop-matches")
    .getByRole("button", { name: "关注阿森纳", exact: true })
    .click();
  await expect(
    page
      .locator(".desktop-matches")
      .getByRole("button", { name: "取消关注阿森纳" }),
  ).toHaveAttribute("aria-pressed", "true");
  await page
    .locator(".desktop-matches")
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "球队对比" })).toBeVisible();
  await page.getByText("阵容情报", { exact: true }).click();
  await expect(page.locator(".pitch")).toHaveCount(2);
  await page.getByText("赔率", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "SP 变化" })).toBeVisible();
  await page.getByRole("button", { name: "历史明细" }).click();
  await expect(page.getByText("SP 历史明细", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByText("AI 分析", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "判断依据" })).toBeVisible();
  await page.getByRole("button", { name: "生成示例版本" }).click();
  await expect(page.getByText("已保存新的示例版本")).toBeVisible();
  await page.getByRole("button", { name: "生成示例版本" }).click();
  expect(
    await page.evaluate(() =>
      JSON.parse(localStorage.getItem("football:v1:demo")).versions[
        "demo-1"
      ].map((v) => v.version),
    ),
  ).toEqual([3, 2, 1]);
  await page
    .getByPlaceholder("记录自己的判断、需要核实的消息或赛后观察…")
    .fill("测试：关注首发变化");
  await page.getByRole("button", { name: "保存笔记" }).click();
  await page.reload();
  await expect(
    page.getByPlaceholder("记录自己的判断、需要核实的消息或赛后观察…"),
  ).toHaveValue("测试：关注首发变化");
  expect(errors).toEqual([]);
});
test("review note persists and export contains records", async ({ page }) => {
  await page.getByRole("link", { name: "复盘记录", exact: true }).click();
  await page.getByRole("button", { name: "查看复盘" }).first().click();
  await page
    .getByPlaceholder("记录偏差原因、可复用的观察和下次改进…")
    .fill("对防守质量估计不足");
  await page.getByRole("button", { name: "保存笔记" }).click();
  await page.reload();
  await page.getByRole("button", { name: "查看复盘" }).first().click();
  await expect(
    page.getByPlaceholder("记录偏差原因、可复用的观察和下次改进…"),
  ).toHaveValue("对防守质量估计不足");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByText("比赛复盘", { exact: true })).not.toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出记录" }).click();
  expect((await download).suggestedFilename()).toContain("复盘记录");
});
test("training configurations validate and persist without claiming execution", async ({
  page,
}) => {
  await page.getByRole("link", { name: "数据与模型", exact: true }).click();
  await page.getByText("训练配置", { exact: true }).first().click();
  await page.getByRole("button", { name: "新建配置" }).click();
  await page.getByLabel("配置名称", { exact: true }).fill("");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByText("请填写配置名称")).toBeVisible();
  await page.getByLabel("配置名称", { exact: true }).fill("测试模型");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByText("待执行", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByText("训练配置", { exact: true }).first().click();
  await expect(page.getByText("测试模型", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("button", { name: "删除", exact: true }).last().click();
  await expect(page.getByText("测试模型", { exact: true })).toHaveCount(0);
});
test("live API errors never fall back to sample data", async ({ page }) => {
  await page.route("**/api/sporttery/matches", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ success: false, error: "测试接口不可用" }),
    }),
  );
  await page.locator(".mode-select").click();
  await page.getByText("真实接口", { exact: true }).last().click();
  await expect(page.getByText("测试接口不可用", { exact: true })).toBeVisible();
  await expect(page.getByText("阿森纳", { exact: true })).toHaveCount(0);
});
test("desktop and mobile screens render without page overflow", async ({
  page,
}) => {
  await page.screenshot({
    path: "test-results/screens/01-matches.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "查看", exact: true }).first().click();
  for (const [label, file] of [
    ["基本面", "02-basic"],
    ["阵容情报", "03-lineups"],
    ["赔率", "04-odds"],
    ["AI 分析", "05-analysis"],
  ]) {
    await page.getByText(label, { exact: true }).first().click();
    await page.screenshot({
      path: `test-results/screens/${file}.png`,
      fullPage: true,
    });
  }
  await page.getByRole("link", { name: "复盘记录", exact: true }).click();
  await page.screenshot({
    path: "test-results/screens/06-reviews.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "数据与模型", exact: true }).click();
  await page.screenshot({
    path: "test-results/screens/07-models.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of [
    "/matches",
    "/match/demo-1/basic",
    "/match/demo-1/analysis",
    "/reviews",
    "/models",
  ]) {
    await page.goto(`/club-preview.html#${path}`);
    await expect(page.locator("h1")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.goto("/club-preview.html#/matches");
  await page.screenshot({
    path: "test-results/screens/08-mobile.png",
    fullPage: true,
  });
});

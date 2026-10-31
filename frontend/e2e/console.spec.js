import { test, expect } from "@playwright/test";

// Test-only fixtures. Production has no demo mode or fixture import.
async function setup(page) {
  const start = new Date(Date.now() + 86400000 * 2).toISOString();
  const market = {
    code: "had",
    label: "胜平负",
    options: [
      { key: "h", label: "主胜", odds: 2 },
      { key: "d", label: "平局", odds: 3.2 },
      { key: "a", label: "客胜", odds: 3.8 },
    ],
    single: false,
    sale_status: "selling",
    updated_at: new Date().toISOString(),
  };
  const match = {
    id: "sporttery:test-1",
    provider: "sporttery",
    provider_id: "test-1",
    number: "测试001",
    home: "测试主队",
    away: "测试客队",
    league: "测试联赛",
    sale_date: start.slice(0, 10),
    kickoff: start,
    markets: {
      had: market,
      ttg: {
        ...market,
        code: "ttg",
        single: true,
        options: [{ key: "s2", label: "2球", odds: 3.5 }],
      },
    },
    sale_status: "selling",
    lifecycle: "upcoming",
    captured_at: new Date().toISOString(),
    analysis_count: 0,
  };
  const state = {
    favorites: [],
    notes: {},
    drafts: {},
    reviewNotes: {},
    versions: {},
  };
  const detail = {
    markets: match.markets,
    history: {
      had: [
        { ...market, updated_at: new Date(Date.now() - 3600000).toISOString() },
        market,
      ],
    },
    captured_at: new Date().toISOString(),
    context: {
      available: true,
      source: "ESPN",
      captured_at: new Date().toISOString(),
      venue: "测试场地",
      recent: [
        [
          {
            id: "r1",
            date: new Date(Date.now() - 86400000 * 3).toISOString(),
            opponent: "历史对手A",
            venue: "主",
            gf: 2,
            ga: 0,
            score: "2-0",
          },
        ],
        [
          {
            id: "r2",
            date: new Date(Date.now() - 86400000 * 4).toISOString(),
            opponent: "历史对手B",
            venue: "客",
            gf: 1,
            ga: 1,
            score: "1-1",
          },
        ],
      ],
      lineups: [
        { status: "unconfirmed", players: [] },
        { status: "unconfirmed", players: [] },
      ],
    },
    errors: [],
    versions: [],
  };
  const capabilities = {
    sources: [],
    models: [
      {
        id: "market-baseline-v1",
        name: "市场基线",
        status: "可用基线",
        method: "完整玩法去水概率",
        validation: "待验证",
      },
    ],
    ai_configured: false,
    match_count: 1,
    analysis_count: 0,
    settled_count: 0,
    storage: "sqlite",
  };
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      p = decodeURIComponent(url.pathname);
    let out;
    if (p === "/api/v1/matches" || p === "/api/v1/sync")
      out = { matches: [match], server_time: new Date().toISOString() };
    else if (p === "/api/v1/state" && req.method() === "GET") out = { state };
    else if (p === "/api/v1/state") {
      const b = req.postDataJSON();
      if (b.kind === "favorites") {
        state.favorites = b.value ? [b.key] : [];
      } else state[b.kind][b.key] = b.value;
      out = {};
    } else if (p === "/api/v1/capabilities") out = capabilities;
    else if (p === "/api/v1/matches/sporttery:test-1") out = { detail };
    else if (p.endsWith("/predict")) {
      const prediction = {
        id: crypto.randomUUID(),
        version: detail.versions.length + 1,
        created: new Date().toISOString(),
        model_version: "market-baseline-v1",
        engine: "市场基线",
        title: "测试主队方向略占优",
        summary: "测试接口返回结果",
        probabilities: [47, 29, 24],
        probLabel: "基线预测概率",
        goals: "2 / 3 球",
        scores: "1-0 / 1-1",
        direction: "主胜",
        evidence: [{ title: "市场基准", text: "已核实市场数据", tab: "odds" }],
        risks: ["首发待确认"],
      };
      detail.versions.unshift(prediction);
      state.versions[match.id] = [...detail.versions];
      out = { prediction, detail };
    } else if (p.startsWith("/api/v1/reviews"))
      out = {
        reviews: detail.versions.length
          ? [
              {
                id: match.id,
                date: match.sale_date,
                home: match.home,
                away: match.away,
                league: match.league,
                pick: "主胜",
                score: "待赛果",
                consistent: null,
                status: "待赛果",
                prediction: detail.versions[0],
                versions: detail.versions,
              },
            ]
          : [],
      };
    else
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ success: false, error: "unknown" }),
      });
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ success: true, ...out }),
    });
  });
  await page.goto("/club-preview.html");
  await expect(
    page.getByRole("heading", { name: "比赛列表", exact: true }),
  ).toBeVisible();
  return { state, detail };
}

test("no demo mode; single eligibility follows selected market", async ({
  page,
}) => {
  await setup(page);
  await expect(page.getByText("示例数据", { exact: true })).toHaveCount(0);
  await page.getByText("仅单关", { exact: true }).click();
  await expect(page.locator(".desktop-matches tbody tr")).toHaveCount(1);
  await page.getByPlaceholder("全部玩法").click();
  await page.getByText("胜平负", { exact: true }).last().click();
  await expect(
    page.getByText(
      "没有符合条件的已采集比赛。可清除筛选，或等待官方公布场次。",
    ),
  ).toBeVisible();
});
test("favorites, source details, predictions and persisted notes", async ({
  page,
}) => {
  const { state } = await setup(page);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.stack));
  await page
    .locator(".desktop-matches")
    .getByRole("button", { name: "关注测试主队", exact: true })
    .click();
  expect(state.favorites).toEqual(["sporttery:test-1"]);
  await page
    .locator(".desktop-matches")
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "球队对比" })).toBeVisible();
  await expect(page.getByText("历史对手A", { exact: true })).toBeVisible();
  await page.getByText("阵容情报", { exact: true }).click();
  await expect(page.getByText("首发待确认", { exact: true })).toHaveCount(2);
  await page.getByText("赔率", { exact: true }).click();
  await page.getByRole("button", { name: "历史明细" }).click();
  await expect(page.getByText("SP 历史明细", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByText("预测分析", { exact: true }).click();
  await page.getByRole("button", { name: "生成预测", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "测试主队方向略占优" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "更新预测", exact: true }).click();
  expect(state.versions["sporttery:test-1"]).toHaveLength(2);
  await page
    .getByPlaceholder("记录自己的判断、需要核实的消息或赛后观察…")
    .fill("持久化笔记");
  await page.getByRole("button", { name: "保存笔记" }).click();
  await page.reload();
  await expect(
    page.getByPlaceholder("记录自己的判断、需要核实的消息或赛后观察…"),
  ).toHaveValue("持久化笔记");
  expect(errors).toEqual([]);
});
test("review saves independent notes; CSV downloads", async ({ page }) => {
  await setup(page);
  await page
    .locator(".desktop-matches")
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await page.getByText("预测分析", { exact: true }).click();
  await page.getByRole("button", { name: "生成预测", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "测试主队方向略占优" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "复盘记录", exact: true }).click();
  await page.getByRole("button", { name: "查看复盘" }).click();
  await page
    .getByPlaceholder("记录判断偏差、关键事件与后续改进…")
    .fill("等赛果后核对");
  await page.getByRole("button", { name: "保存笔记" }).click();
  await page.reload();
  await page.getByRole("button", { name: "查看复盘" }).click();
  await expect(
    page.getByPlaceholder("记录判断偏差、关键事件与后续改进…"),
  ).toHaveValue("等赛果后核对");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出记录" }).click();
  expect((await download).suggestedFilename()).toBe("复盘记录.csv");
});
test("source failure cannot fabricate matches", async ({ page }) => {
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ success: false, error: "来源暂不可用" }),
    }),
  );
  await page.goto("/club-preview.html");
  await expect(
    page.getByRole("heading", { name: "暂时无法获取比赛" }),
  ).toBeVisible();
  await expect(page.locator(".fixture-link")).toHaveCount(0);
  await expect(page.getByText("示例数据", { exact: true })).toHaveCount(0);
});
test("model coverage and responsive pages", async ({ page }) => {
  await setup(page);
  await page.getByRole("link", { name: "数据与模型", exact: true }).click();
  await page.getByText("预测模型", { exact: true }).click();
  await expect(
    page.getByText("market-baseline-v1", { exact: true }),
  ).toBeVisible();
  await page.getByText("训练与评估", { exact: true }).click();
  await expect(
    page.getByText("训练服务未接入，基线模型未校准。"),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of [
    "/matches",
    "/match/sporttery:test-1/basic",
    "/match/sporttery:test-1/analysis",
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
});

import { test, expect } from "@playwright/test";

test("AI postmatch review is separate from editable notes and supports retry", async ({
  page,
}) => {
  const { match, state } = await setup(page);
  let review = { status: "failed", error: "复盘服务暂不可用" };
  const prediction = {
    id: "test-archive",
    version: 1,
    created: "2026-10-31T06:00:00Z",
    model_version: "test-model",
    title: "赛前归档",
    evidence: [],
    direction: "主胜",
  };
  await page.route("**/api/v1/reviews**", async (route) => {
    if (route.request().url().endsWith("/generate")) {
      expect(route.request().postDataJSON().match_id).toBe(match.id);
      review = {
        status: "completed",
        model: "test-model",
        updated_at: new Date().toISOString(),
        report: {
          summary: "方向覆盖，比赛过程原因无法确认。",
          findings: [
            {
              text: "原记录为主胜，正式赛果为2-0。",
              evidence_ids: ["official-result"],
            },
          ],
          hypotheses: [],
          missing_data: ["比赛事件"],
          next_checks: ["核实正式事件记录"],
        },
        evidence: [
          {
            id: "official-result",
            kind: "official_result",
            data: { score: "2-0" },
          },
        ],
      };
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        ai_review_config: { enabled: true, configured: true },
        reviews: [
          {
            ...match,
            date: "2026-10-31",
            pick: "主胜",
            score: "2-0",
            status: "待复盘",
            prediction,
            versions: [prediction],
            ai_review: review,
          },
        ],
      }),
    });
  });
  await page.goto("/club-preview.html#/reviews");
  await expect(page.getByText("自动复盘已开启", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看复盘" }).click();
  await page
    .getByPlaceholder("记录判断偏差、关键事件与后续改进…")
    .fill("尚未保存的个人笔记");
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(
    page.getByText("方向覆盖，比赛过程原因无法确认。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByPlaceholder("记录判断偏差、关键事件与后续改进…"),
  ).toHaveValue("尚未保存的个人笔记");
  expect(state.reviewNotes[match.id]).toBeUndefined();
  await page.getByText("查看依据", { exact: true }).click();
  await expect(page.getByText("2-0（90分钟）", { exact: true })).toBeVisible();
});

test("competition selection scopes datasets, training and observation selection", async ({
  page,
}) => {
  const { capabilities } = await setup(page);
  capabilities.competitions = [
    { id: "jp.j1", name: "日职", seasons: [] },
    { id: "kr.k1", name: "韩职", seasons: [] },
  ];
  capabilities.competition_workspaces = Object.fromEntries(
    capabilities.competitions.map((c, i) => [
      c.id,
      {
        competition: c,
        competitions: [c],
        sources: [],
        fundamentals: [],
        odds: [],
        training: {
          ...structuredClone(capabilities.training),
          ready: true,
          reason: "可训练与回测",
          rows: i ? 884 : 1391,
        },
        models: [
          {
            id: c.id,
            run_id: c.id + "-candidate",
            name: c.name + "独立模型",
            status: "未选用",
            validation_status: "离线已评估",
            selected: false,
          },
        ],
      },
    ]),
  );
  const actions = [];
  for (const route of ["training/evaluate", "models/select", "datasets/import"])
    await page.route("**/api/v1/" + route, async (r) => {
      actions.push({ route, ...r.request().postDataJSON() });
      await r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ success: true }),
      });
    });
  await page.goto("/club-preview.html#/models");
  await expect(page.locator(".summary-strip")).toContainText("1391");
  await page.getByText("预测模型", { exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "日职独立模型", exact: true }),
  ).toBeVisible();
  await page.locator(".competition-selector").click();
  await page.getByText("韩职", { exact: true }).last().click();
  await expect(page.locator(".summary-strip")).toContainText("884");
  await expect(
    page.getByRole("cell", { name: "日职独立模型", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "选用观察" }).click();
  await expect.poll(() => actions.length).toBe(1);
  expect(actions[0]).toMatchObject({
    competition_id: "kr.k1",
    run_id: "kr.k1-candidate",
  });
  await page.getByText("训练与评估", { exact: true }).click();
  await page.getByRole("button", { name: "训练与回测", exact: true }).click();
  await expect.poll(() => actions.length).toBe(2);
  expect(actions[1]).toMatchObject({ competition_id: "kr.k1", target: "full_time" });
  await page.locator('input[type="file"]').setInputFiles({
    name: "history.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({ manifest: {}, matches: [] })),
  });
  await expect.poll(() => actions.length).toBe(3);
  expect(actions[2]).toMatchObject({
    competition_id: "kr.k1",
    dataset: { matches: [] },
  });
  await page.reload();
  await expect(page.locator(".summary-strip")).toContainText("884");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

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
    training: {
      ready: false,
      rows: 0,
      reason: "等待授权历史数据",
      season_coverage: [],
      staging: {
        available: true,
        status: "collected",
        rights_status: "unverified",
        excluded: [],
        coverage: [
          {
            id: "jp.j1:2023",
            competition_id: "jp.j1",
            season: "2023",
            collected: 306,
            expected: 306,
            half_matches: 306,
            verified: 2,
            issues: [],
            status: "待入库",
          },
        ],
      },
    },
  };
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      p = decodeURIComponent(url.pathname);
    let out;
    if (p === "/api/v1/matches" || p === "/api/v1/sync")
      out = { matches: [match], server_time: new Date().toISOString() };
    else if (p === "/api/v1/scores")
      out = { scores: { [match.id]: match.live_score } };
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
        market_assessments: [
          {
            code: "had",
            label: "胜平负",
            origin: "market",
            selection: "主胜",
            selected_keys: ["h"],
            selection_count: 1,
            coverage: 47,
            options: market.options.map((o, i) => ({
              ...o,
              probability: [47, 29, 24][i],
            })),
          },
          {
            code: "hhad",
            label: "让球胜平负",
            origin: "market",
            line: -1,
            selection: "观望",
            selected_keys: [],
            selection_count: 0,
            coverage: 0,
            options: [],
          },
          {
            code: "ttg",
            label: "总进球",
            origin: "market",
            selection: "2球 / 3球",
            selected_keys: ["s2", "s3"],
            selection_count: 2,
            coverage: 51,
            options: [4, 15, 26, 25, 16, 8, 4, 2].map((p, i) => ({
              key: `s${i}`,
              label: `${i}球`,
              probability: p,
              odds: 4,
            })),
          },
          {
            code: "crs",
            label: "比分",
            origin: "market",
            selection: "1-0 / 1-1",
            selected_keys: ["s01s00", "s01s01"],
            selection_count: 2,
            coverage: 20,
            options: [],
          },
          {
            code: "hafu",
            label: "半全场",
            origin: "market",
            selection: "观望",
            selected_keys: [],
            selection_count: 0,
            coverage: 0,
            options: [],
          },
        ],
        football_model: {
          status: "unavailable",
          reason: "尚无已训练的足球模型",
          markets: [],
        },
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
                live_score: match.live_score,
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
  return { state, detail, match, capabilities };
}

test("league multi-select combines with saved prediction status and resets", async ({
  page,
}) => {
  const { match, state } = await setup(page);
  const matches = ["日职", "韩职", "英超"].map((league, i) => ({
    ...match,
    id: `filter-${i}`,
    league,
    home: `筛选主队${i}`,
  }));
  state.versions[matches[0].id] = [{ id: "saved-prediction" }];
  await page.route("**/api/v1/matches", (route) =>
    route.fulfill({
      json: { success: true, matches, server_time: new Date().toISOString() },
    }),
  );
  await page.reload();
  const rows = page.locator(".desktop-matches .fixture-link");
  await expect(rows).toHaveCount(3);
  await page.getByPlaceholder("全部赛事").click();
  await page
    .locator(".t-select-option")
    .getByText("日职", { exact: true })
    .click();
  await page
    .locator(".t-select-option")
    .getByText("韩职", { exact: true })
    .click();
  await page.getByRole("heading", { name: "比赛列表", exact: true }).click();
  await expect(rows).toHaveCount(2);
  await page.locator("#prediction-filter").click();
  await page
    .locator(".t-select-option")
    .getByText("已预测", { exact: true })
    .click();
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText(["筛选主队0"]);
  await page.locator("#prediction-filter").click();
  await page
    .locator(".t-select-option")
    .getByText("未预测", { exact: true })
    .click();
  await expect(rows).toContainText(["筛选主队1"]);
  await page.getByRole("button", { name: "重置筛选" }).click();
  await expect(rows).toHaveCount(3);
  await expect(page.getByPlaceholder("全部赛事")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#prediction-filter")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("no demo mode; single eligibility follows selected market", async ({
  page,
}) => {
  await setup(page);
  await expect(page.getByText("示例数据", { exact: true })).toHaveCount(0);
  // A single-eligible total-goals pool must not label the displayed HAD prices as singles.
  await expect(page.locator(".desktop-matches .market-single")).toHaveCount(0);
  await expect(
    page.locator(".desktop-matches").getByText("含单关", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page
      .locator(".desktop-matches")
      .getByRole("columnheader", { name: "销售", exact: true }),
  ).toBeVisible();
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
  await page.getByRole('button', { name: '详情', exact: true }).first().click();
  await expect(
    page.getByText("market-baseline-v1", { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByText("训练与评估", { exact: true }).click();
  await expect(page.getByText("等待授权历史数据")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "历史训练数据", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "306 / 306", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "待验收", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "训练与回测", exact: true }),
  ).toBeDisabled();
  await page.getByText("赛事覆盖", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "赛事与赛季", exact: true }),
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

test("model workspace keeps rows single-line and details accessible", async ({
  page,
}) => {
  const { capabilities } = await setup(page);
  capabilities.models.push(
    {
      id: "club-goals-v2:" + "a".repeat(64),
      name: "足球进球模型",
      status: "待训练",
      validation_status: "未评估",
      method: "攻防泊松 / 低比分校正 / 条件半全场",
      validation: "历史数据待验收",
    },
    {
      id: "retired-v1",
      name: "停用模型",
      status: "已停用",
      validation: "未经回测的固定权重已退出新预测",
    },
  );
  capabilities.sources = [
    {
      id: "live-scores",
      name: "实时比分",
      status: "partial",
      reason: "36 场未匹配",
    },
  ];
  capabilities.provider_configuration = [
    {
      id: "paid",
      name: "付费数据源",
      configured: false,
      status: "not_configured",
    },
  ];
  capabilities.training.reason = "历史数据待验收";
  capabilities.training.blockers = [
    "来源使用依据待确认",
    "日职 2023：1 场时间差异待核查",
  ];
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.goto("/club-preview.html#/models");
  await expect(
    page.getByRole("cell", { name: "实时比分", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "500 历史赛果", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "付费数据源", exact: true }),
  ).toHaveCount(0);
  await page.getByText("显示未配置", { exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "付费数据源", exact: true }),
  ).toBeVisible();
  await page.getByText("预测模型", { exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(2);
  expect(
    await page
      .locator(".t-table__content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  ).toBe(true);
  expect(
    await page
      .locator("tbody tr")
      .evaluateAll((rows) =>
        rows.every((r) => r.getBoundingClientRect().height <= 60),
      ),
  ).toBe(true);
  await page
    .getByRole("row")
    .filter({ hasText: "足球进球模型" })
    .getByRole("button", { name: "详情" })
    .click();
  await expect(
    page
      .locator(".t-drawer")
      .getByText("攻防泊松 / 低比分校正 / 条件半全场", { exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByText("显示停用模型", { exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(3);
  await page.getByText("训练与评估", { exact: true }).click();
  await page.getByRole("button", { name: "查看原因" }).click();
  await expect(
    page.locator(".t-drawer").getByText(/来源使用依据待确认/),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("five markets expose archived probabilities without an LLM action", async ({
  page,
}) => {
  await setup(page);
  await page
    .locator(".desktop-matches")
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await page.getByText("预测分析", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "AI 解读", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "生成预测", exact: true }).click();
  const panel = page.locator("section").filter({
    has: page.getByRole("heading", { name: "玩法分析", exact: true }),
  });
  await expect(panel.locator("tbody tr")).toHaveCount(5);
  await panel
    .getByRole("button", { name: "查看概率", exact: true })
    .first()
    .click();
  await expect(
    page.locator(".t-drawer").getByText("47.0%", { exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByText("尚无已训练的足球模型", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.ai/tmp/football-model-analysis.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("live page includes closed matches, isolates delayed scores and blocks pregame updates", async ({
  page,
}) => {
  const { match } = await setup(page);
  match.sale_status = "closed";
  match.lifecycle = "live";
  match.live_score = {
    score: "1-0",
    state: "live",
    label: "下半场",
    clock: "62′",
    observed_at: new Date().toISOString(),
    checked_at: new Date().toISOString(),
    source: "ESPN",
    source_url: "https://www.espn.com/",
    stale: false,
  };
  await page.getByRole("link", { name: "实时比分", exact: true }).click();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.locator(".desktop-matches .live-score-value")).toHaveText(
    "1 – 0",
  );
  await expect(page.locator(".desktop-matches .live-score-phase")).toHaveText(
    "下半场 · 62′",
  );
  await expect(
    page.getByRole("checkbox", { name: "仅在售", exact: true }),
  ).toHaveCount(0);
  match.live_score.observed_at = new Date(Date.now() - 300000).toISOString();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.locator(".desktop-matches .live-score-phase")).toHaveText(
    "更新延迟",
  );
  await expect(page.locator(".desktop-matches .live-score-value")).toHaveText(
    "1 – 0",
  );
  await page
    .locator(".desktop-matches")
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await expect(page.locator(".match-header .live-score-value")).toHaveText(
    "1 – 0",
  );
  await page.getByText("预测分析", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "生成预测", exact: true }),
  ).toBeDisabled();
});

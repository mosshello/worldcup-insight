import { test, expect } from "@playwright/test";

for (const startRoute of ['reviews', 'matches']) {
  test(`review notification opens its match from ${startRoute} and reopens without losing drafts`, async ({ page }) => {
    const { match } = await setup(page);
    const first = { ...match, home: '通知主队', away: '通知客队', date: '2026-11-04',
      score: '2-1', status: '已分析', prediction: null, versions: [],
      has_prediction: false, ai_review: { status: 'no_prediction' },
      postmatch_review: { status: 'confirmed', summary: '通知对应比赛', findings: [], recommendations: [] } };
    const second = { ...first, id: 'sporttery:second', home: '另一主队', away: '另一客队' };
    await page.route('**/api/v1/reviews', route => route.fulfill({ json: {
      success: true, reviews: [first, second], ai_review_config: { configured: false, enabled: true },
    } }));
    await page.route('**/api/v1/review-updates*', route => {
      const after = new URL(route.request().url()).searchParams.get('after');
      return route.fulfill({ json: { success: true, cursor: after === null ? 0 : 1,
        updates: after === '0' ? [{ id: 1, match_id: first.id,
          fixture: '通知主队 vs 通知客队', status: 'confirmed' }] : [] } });
    });
    await page.goto(`/club-preview.html#/${startRoute}`);
    const notice = page.locator('main > [role=status]').getByRole('link', { name: '查看复盘' });
    await expect(notice).toBeVisible({ timeout: 10000 });
    await expect(notice).toHaveAttribute('href', `#/reviews/${encodeURIComponent(first.id)}`);
    await notice.click();
    const drawer = page.locator('.t-drawer--open');
    const note = page.getByPlaceholder('记录判断偏差、关键事件与后续改进…');
    await expect(drawer.getByRole('heading', { name: '通知主队 vs 通知客队' })).toBeVisible();
    await note.fill('通知比赛的未保存笔记');
    await drawer.getByRole('button', { name: '关闭', exact: true }).click();
    await expect(drawer).toHaveCount(0);
    // Same hash: closing then clicking the same notification must open again.
    await notice.click();
    await expect(note).toHaveValue('通知比赛的未保存笔记');
    await drawer.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByRole('row').filter({ hasText: '另一主队' }).getByRole('button', { name: '查看复盘' }).click();
    await note.fill('另一场的未保存笔记');
    await drawer.getByRole('button', { name: '关闭', exact: true }).click();
    await notice.click();
    await expect(drawer.getByRole('heading', { name: '通知主队 vs 通知客队' })).toBeVisible();
    await expect(note).toHaveValue('通知比赛的未保存笔记');
    await drawer.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByRole('row').filter({ hasText: '另一主队' }).getByRole('button', { name: '查看复盘' }).click();
    await expect(note).toHaveValue('另一场的未保存笔记');
  });
}

test('validated cold-start predictions show actual samples and parameter uncertainty only', async ({ page }) => {
  const { match, detail } = await setup(page);
  const model = { cold_start_policy: { policy: 'gamma_cold_start_v1', passed: true }, team_samples: [3, 9],
    uncertainty: { level: 0.9, interpretation: 'parameter_uncertainty_not_match_score_interval',
      goal_rate_intervals: [[0.5, 2.1], [0.8, 2.8]] } };
  detail.versions = [{ id: 'cold-start-test', version: 1, schema_version: 2, created: new Date().toISOString(),
    title: '测试预测', probabilities: [40, 30, 30], football_model: model, evidence: [], risks: [] }];
  await page.goto(`/club-preview.html#/match/${match.id}/analysis`);
  await expect(page.getByTestId('cold-start-status')).toContainText('主 3 / 客 9 场');
  await page.getByRole('button', { name: '参数区间', exact: true }).click();
  const drawer = page.locator('.t-drawer--open').filter({ hasText: '预期进球参数区间' });
  await expect(drawer).toContainText('90% 参数区间');
  await expect(drawer).toContainText('0.50 – 2.10');
  await expect(drawer).not.toContainText('比分区间');
  model.cold_start_policy.passed = false;
  await page.reload();
  await expect(page.getByRole('heading', { name: '测试预测', exact: true })).toBeVisible();
  await expect(page.getByTestId('cold-start-status')).toHaveCount(0);
});

test("legacy directions stay distinct and match analysis links to its review", async ({
  page,
}) => {
  const { match, detail } = await setup(page);
  detail.legacy_prediction = {
    direction: "客胜",
    score: "1-2",
    recorded_at: new Date().toISOString(),
    recorded_before_kickoff: true,
    notice: "旧日志仅供查阅，不计入新版命中与模型评估。",
  };
  detail.intelligence = {
    phase: "pregame",
    lineups: [{ side: "home", summary: "测试主队：首发待确认" }],
    markets: [],
    evidence: [],
    checks: ["伤停尚未完整核实"],
    limitations: ["不能证明操控意图"],
  };
  await page.route("**/api/v1/reviews", async (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        reviews: [
          {
            ...match,
            date: "2026-11-03",
            pick: "未归档",
            score: "待赛果",
            has_prediction: false,
            prediction: null,
            legacy_prediction: detail.legacy_prediction,
            consistent: null,
            postmatch_review: { status: "waiting", reason: "等待全场结束" },
            ai_review: { status: "no_prediction" },
            market_results: [],
            model_results: [],
            intelligence_review: { current: detail.intelligence },
          },
        ],
      }),
    }),
  );
  await page.goto(`/club-preview.html#/match/${match.id}/analysis`);
  await expect(
    page.getByRole("heading", { name: "当前分析线索与核查建议" }),
  ).toBeVisible();
  await expect(page.getByLabel("旧版预测记录")).toContainText("客胜");
  await page.getByRole("link", { name: "查看关联复盘", exact: true }).click();
  await expect(page.locator(".t-drawer")).toBeVisible();
  await expect(page.locator(".t-drawer")).toContainText("不计入新版命中");
  await expect(page.locator(".t-table").first()).toContainText("客胜 · 旧版");
  await expect(page.locator(".t-table").first()).toContainText("无赛前对照");
});

test("finished match without pregame archive exposes factual analysis and export", async ({
  page,
}) => {
  const { match } = await setup(page);
  await page.route("**/api/v1/reviews**", async (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        reviews: [
          {
            ...match,
            date: "2026-11-03",
            pick: "未归档",
            score: "4-3",
            status: "已分析",
            has_prediction: false,
            prediction: null,
            versions: [],
            consistent: null,
            market_results: [],
            model_results: [],
            ai_review: { status: "no_prediction" },
            live_score: {
              score: "4-3",
              state: "completed",
              label: "全场结束",
              stale: false,
            },
            postmatch_review: {
              id: 1,
              status: "confirmed",
              score: "4-3",
              summary: "90分钟比分4-3，主胜，总进球7。",
              findings: ["本场没有赛前预测归档，仅作赛后事实分析。"],
              recommendations: ["核实本场事件资料。"],
              missing_data: ["射门与xG"],
              source: "测试来源",
              created_at: new Date().toISOString(),
            },
          },
        ],
      }),
    }),
  );
  await page.goto("/club-preview.html#/reviews");
  await expect(
    page.getByRole("cell", { name: "无赛前对照", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看复盘", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "赛后事实", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".t-drawer")).toContainText("本场没有赛前预测归档");
  await expect(
    page.getByRole("heading", { name: "赛前判断 · 只读" }),
  ).toHaveCount(0);
  await page
    .locator(".t-drawer")
    .getByRole("button", { name: "关闭", exact: true })
    .click();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出记录", exact: true }).click();
  expect((await downloaded).suggestedFilename()).toBe("复盘记录.csv");
  await page.setViewportSize({ width: 850, height: 900 });
  const positions = await page
    .locator(".review-summary > div")
    .evaluateAll((nodes) => nodes.map((n) => n.getBoundingClientRect().top));
  expect(new Set(positions).size).toBe(1);
});

test("full-time analysis updates before official settlement and preserves notes", async ({
  page,
}) => {
  const { match } = await setup(page);
  let report = { status: "waiting", reason: "等待比分源确认全场结束" };
  let official = "待赛果";
  const prediction = {
    id: "pre-game",
    version: 1,
    created: "2026-11-01T03:00:00Z",
    model_version: "test-model",
    direction: "主胜",
    title: "赛前归档",
    evidence: [],
  };
  await page.route("**/api/v1/reviews**", async (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        ai_review_config: { configured: false, enabled: true },
        reviews: [
          {
            ...match,
            date: "2026-11-01",
            pick: "主胜",
            score: official,
            status: official === "待赛果" ? "待赛果" : "待复盘",
            consistent: null,
            prediction,
            versions: [prediction],
            postmatch_review: report,
            ai_review: { status: "waiting_result" },
          },
        ],
      }),
    }),
  );
  await page.route("**/api/v1/review-updates*", async (route) => {
    const after = new URL(route.request().url()).searchParams.get("after");
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        cursor: report.id || 0,
        updates:
          after !== null && Number(after) < (report.id || 0)
            ? [{ ...report, match_id: match.id, fixture: "测试主队 vs 测试客队" }]
            : [],
      }),
    });
  });
  await page.goto("/club-preview.html#/reviews");
  await page.getByRole("button", { name: "查看复盘", exact: true }).click();
  const drawer = page.locator(".t-drawer");
  await expect(drawer).toContainText("等待比分源确认全场结束");
  const note = page.getByPlaceholder("记录判断偏差、关键事件与后续改进…");
  await note.fill("保留未保存的本场观察");
  report = {
    id: 1,
    status: "preliminary",
    score: "1-2",
    summary: "完场初评：90分钟比分1-2，客胜。",
    findings: ["赛前主胜与完场结果存在偏差。", "缺少事件证据，原因无法确认。"],
    recommendations: ["累计同赛事样本后核查概率校准。"],
    missing_data: ["射门与xG"],
    source: "测试比分源",
    observed_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    prediction_version: 1,
  };
  await expect(
    drawer.getByRole("heading", { name: "完场初评 · 待正式确认" }),
  ).toBeVisible({ timeout: 10000 });
  await expect(drawer).toContainText("累计同赛事样本后核查概率校准");
  await expect(note).toHaveValue("保留未保存的本场观察");
  await expect(page.locator("main > [role=status]")).toContainText(
    "完场初评已生成",
    { timeout: 10000 },
  );
  await expect(page.locator(".t-table").first()).toContainText("待赛果");
  official = "2-1";
  report = {
    ...report,
    id: 2,
    status: "confirmed",
    score: "2-1",
    previous_score: "1-2",
    summary: "赛果已确认：90分钟比分2-1，主胜。",
  };
  await expect(
    drawer.getByRole("heading", { name: "完场分析 · 已确认" }),
  ).toBeVisible({ timeout: 10000 });
  await expect(drawer).toContainText("旧分析已保留");
  await expect(note).toHaveValue("保留未保存的本场观察");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

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
          {
            id: "match-process",
            kind: "postmatch_process",
            data: { statistics: [{label:'射门',home:'12',away:'5'}], events: [
              {period_label:'常规时间',clock:"43'",team:'主队',label:'进球'}
            ] },
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
  await expect(page.getByText('本场比赛统计与事件', {exact:true})).toBeVisible();
  await expect(page.getByText('射门（主 / 客）：12 / 5', {exact:false})).toBeVisible();
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
    quality: { can_predict: true, reasons: [] },
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
    else if (p === "/api/v1/review-updates") out = { cursor: 0, updates: [] };
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
test("missing fundamentals blocks prediction and explains why beside the button", async ({ page }) => {
  const { detail, state } = await setup(page);
  detail.quality = { can_predict: false, reasons: ["双方伤停与本赛事参赛资格未完整核查"] };
  await page.locator(".desktop-matches").getByRole("button", { name: "查看", exact: true }).click();
  await page.getByText("预测分析", { exact: true }).click();
  await expect(page.getByRole("button", { name: "生成预测", exact: true })).toBeDisabled();
  await expect(page.getByRole("alert").filter({ hasText: "双方伤停与本赛事参赛资格未完整核查" })).toBeVisible();
  expect(state.versions).toEqual({});
});

test("failed detail request never enables prediction", async ({ page }) => {
  const { state } = await setup(page);
  await page.route('**/api/v1/matches/sporttery%3Atest-1*', route => route.fulfill({status:503, contentType:'application/json', body:JSON.stringify({success:false,error:'数据源不可用'})}));
  await page.locator('.desktop-matches').getByRole('button', {name:'查看',exact:true}).click();
  await page.getByText('预测分析', {exact:true}).click();
  await expect(page.getByRole('button', {name:'生成预测',exact:true})).toBeDisabled();
  expect(state.versions).toEqual({});
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

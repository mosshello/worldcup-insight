import { test, expect } from "@playwright/test";
async function setup(page) {
  const writes = [];
  const workspace = {
    competition: { id: "jp.j1", training_enabled: true },
    overview: {
      date: "2026-11-04",
      total: 2,
      eligible: 1,
      blocked: 1,
      unknown: 0,
      scope: "已收录比赛",
      checked_at: "2026-11-04T00:00:00Z",
      matches: [
        { id: "m", name: "主队 — 客队", status: "blocked", reason: "未知球队" },
      ],
    },
    teams: [
      {
        id: "t",
        name: "测试球队",
        season: "2026",
        status: "active",
        source_ids: { official: "123" },
      },
    ],
    datasets: {
      current_digest: "digest-a",
      versions: [{ id: "d", digest: "digest-a", rows: 400 }],
      season_coverage: [
        { id: "s", season: "2023", collected: 306, imported: 305, excluded: 1 },
      ],
    },
    issues: [
      { id: "q", name: "冲突比赛", reason: "开球时间冲突", status: "blocked" },
    ],
    tasks: [],
    training: {
      model: { run_id: "selected-poisson", dataset_digest: "old-digest" },
      candidate: { run_id: "candidate-gamma" },
      dataset_digest: "digest-a",
      selection_revision: 2,
    },
    models: [
      {
        id: "m1",
        run_id: "selected-poisson",
        name: "攻防泊松",
        status: "前瞻观察",
      },
      {
        id: "m2",
        run_id: "candidate-gamma",
        name: "动态 Gamma–Poisson",
        status: "候选",
      },
    ],
    sources: [],
    fundamentals: [],
    odds: [],
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body = { success: true };
    if (url.pathname === "/api/v2/workspaces")
      body = {
        ...body,
        competitions: [{ id: "jp.j1", name: "日职" }],
        workspace,
      };
    else if (url.pathname.includes("/runs/"))
      body = {
        ...body,
        report: {
          run_id: url.pathname.split("/").at(-1),
          report_type: "fixed_artifact",
          metrics: { log_loss: 1.23 },
        },
      };
    else if (route.request().method() === "POST") {
      writes.push({ path: url.pathname, body: route.request().postDataJSON() });
    } else if (url.pathname.endsWith("/matches")) body.matches = [];
    else if (url.pathname.endsWith("/state"))
      Object.assign(body, {
        favorites: [],
        notes: {},
        versions: {},
        reviewNotes: {},
        drafts: {},
      });
    else if (url.pathname.endsWith("review-updates"))
      Object.assign(body, { updates: [], cursor: 0 });
    await route.fulfill({ json: body });
  });
  await page.goto(
    (test.info().project.metadata.previewPath || "/club-preview.html") +
      "#/models",
  );
  await expect(page.getByText("赛事概览", { exact: true })).toBeVisible();
  return { writes, workspace };
}
test("six pages show traceable data and selected model exact report", async ({
  page,
}) => {
  await setup(page);
  for (const name of [
    "赛事概览",
    "球队与参赛",
    "数据接入",
    "数据集与质量",
    "训练与评估",
    "模型与使用",
  ])
    await expect(page.getByText(name, { exact: true })).toBeVisible();
  await page.getByText("数据集与质量", { exact: true }).click();
  await expect(page.getByText("开球时间冲突")).toBeVisible();
  await page.getByText("模型与使用", { exact: true }).click();
  await page.getByRole("button", { name: "查看报告", exact: true }).click();
  const drawer = page.locator(".t-drawer--open");
  await expect(drawer).toContainText("selected-poisson");
  await expect(drawer).not.toContainText("candidate-gamma");
});
test("fit creates one explicit task and never selects model", async ({
  page,
}) => {
  const { writes } = await setup(page);
  await page.getByText("训练与评估", { exact: true }).click();
  await page.getByRole("button", { name: "新建任务" }).click();
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toEqual({
    path: "/api/v2/competitions/jp.j1/tasks",
    body: {
      kind: "fit",
      target: "full_time",
      dataset_digest: "digest-a",
      algorithm: "poisson",
      half_life: 180,
    },
  });
});

test('fixed evaluation validates its date range and experiment stays a separate task', async ({ page }) => {
  const { writes } = await setup(page);
  await page.getByText('训练与评估', { exact: true }).click();
  await page.getByRole('button', { name: '新建任务', exact: true }).click();
  await page.getByLabel('任务类型').locator('input').click();
  await page.locator('.t-select-option').filter({ hasText: /^固定模型评估$/ }).click();
  await page.getByRole('button', { name: '创建任务', exact: true }).click();
  await expect(page.locator('.t-drawer--open')).toContainText('请选择评估模型');
  expect(writes).toHaveLength(0);
  await page.getByLabel('评估模型').locator('input').click();
  await page.locator('.t-select-option').filter({ hasText: /^selected-poisson$/ }).click();
  await page.getByLabel('开始日期').fill('2026-11-01');
  await page.getByLabel('结束日期').fill('2026-10-01');
  await page.getByRole('button', { name: '创建任务', exact: true }).click();
  await expect(page.locator('.t-drawer--open')).toContainText('请填写有效评估区间');
  expect(writes).toHaveLength(0);
  await page.getByLabel('结束日期').fill('2026-11-04');
  await page.getByRole('button', { name: '创建任务', exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body).toEqual({ kind:'evaluate', target:'full_time', dataset_digest:'digest-a',
    run_id:'selected-poisson', date_from:'2026-11-01', date_to:'2026-11-04' });
  await page.getByRole('button', { name: '新建任务', exact: true }).click();
  await page.getByLabel('任务类型').locator('input').click();
  await page.locator('.t-select-option').filter({ hasText: /^滚动实验$/ }).click();
  await page.getByRole('button', { name: '运行实验', exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]).toEqual({ path:'/api/v2/competitions/jp.j1/tasks',
    body:{kind:'experiment', target:'full_time',dataset_digest:'digest-a'} });
  expect(writes.every(w => !w.path.endsWith('/selection'))).toBe(true);
});

test('workspace load errors leave a usable retry instead of an endless spinner', async ({ page }) => {
  await setup(page);
  await page.route('**/api/v2/workspaces*', route => route.fulfill({ status:503,
    json:{success:false,error:'测试：模型资料读取暂时失败'} }));
  await page.getByRole('button', { name:'刷新状态',exact:true }).click();
  await expect(page.getByText('测试：模型资料读取暂时失败', {exact:true}).filter({visible:true})).toBeVisible();
  await expect(page.getByRole('button', { name:'刷新状态',exact:true })).toBeEnabled();
  await expect(page.getByText('赛事概览', {exact:true})).toBeVisible();
  await page.unroute('**/api/v2/workspaces*');
  await page.getByRole('button', { name:'刷新状态',exact:true }).click();
  await expect(page.getByText('测试：模型资料读取暂时失败', {exact:true})).toHaveCount(0);
});

test("tables keep single lines and mobile scroll stays inside panels", async ({
  page,
}) => {
  await setup(page);
  await page.getByText("数据集与质量", { exact: true }).click();
  expect(
    await page
      .locator(".t-table td")
      .evaluateAll((cells) =>
        cells.every((c) => getComputedStyle(c).whiteSpace === "nowrap"),
      ),
  ).toBe(true);
  await page.getByRole("button", { name: "详情", exact: true }).last().click();
  await expect(page.locator(".t-drawer--open")).toContainText("开球时间冲突");
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.design/football-data-model-refactor/mobile.png",
    fullPage: true,
  });
});

test("competition switch scopes selection, task and imported dataset", async ({
  page,
}) => {
  const { workspace, writes } = await setup(page);
  await page.route("**/api/v2/workspaces?**", async (route) => {
    const cid = new URL(route.request().url()).searchParams.get(
      "competition_id",
    );
    const selected = structuredClone(workspace);
    selected.competition.id = cid;
    selected.training.model = null;
    selected.models = [
      {
        id: cid,
        run_id: cid + "-candidate",
        name: cid === "jp.j1" ? "日职独立模型" : "韩职独立模型",
        status: "候选",
      },
    ];
    await route.fulfill({
      json: {
        success: true,
        competitions: [
          { id: "jp.j1", name: "日职" },
          { id: "kr.k1", name: "韩职" },
        ],
        workspace: selected,
      },
    });
  });
  await page.getByRole("button", { name: "刷新状态" }).click();
  await page.locator(".competition-selector").click();
  await page.getByText("韩职", { exact: true }).last().click();
  await page.getByText("模型与使用", { exact: true }).click();
  await expect(page.getByText("韩职独立模型", { exact: true })).toBeVisible();
  await expect(page.getByText("日职独立模型", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "选用观察", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toEqual({
    path: "/api/v2/competitions/kr.k1/selection",
    body: { run_id: "kr.k1-candidate", expected_revision: 2 },
  });
  await page.getByText("训练与评估", { exact: true }).click();
  await page.getByRole("button", { name: "新建任务" }).click();
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].path).toBe("/api/v2/competitions/kr.k1/tasks");
  await page.getByText("数据集与质量", { exact: true }).click();
  await page
    .locator("input[type=file]")
    .setInputFiles({
      name: "history.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({ manifest: { competition_id: "kr.k1" }, matches: [] }),
      ),
    });
  await expect.poll(() => writes.length).toBe(3);
  expect(writes[2].path).toBe("/api/v2/competitions/kr.k1/datasets/import");
  await page.reload();
  await expect(page.locator(".competition-selector input")).toHaveValue("韩职");
});

test("history ingestion is explicit and never imports or trains", async ({
  page,
}) => {
  const { workspace, writes } = await setup(page);
  workspace.ingestion_supported = true;
  await page.getByRole("button", { name: "刷新状态" }).click();
  await page.getByText("数据接入", { exact: true }).click();
  await page.getByRole("button", { name: "采集历史", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].path).toBe("/api/v2/competitions/jp.j1/ingestion");
  expect(writes[0].body.request_key).toMatch(/^[0-9a-f-]{36}$/);
});

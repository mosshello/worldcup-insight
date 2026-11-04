<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { RefreshIcon } from "tdesign-icons-vue-next";
import { api, timeLabel } from "./api";
const tab = ref("sources"),
  loading = ref(false),
  error = ref(""),
  payload = ref(null),
  evaluating = ref(false),
  showRetired = ref(false),
  showUnconfigured = ref(false),
  drawer = ref(null);
const selectedCompetition = ref(
  localStorage.getItem("football:model-competition") || "jp.j1",
);
const importing = ref(false),
  selecting = ref(false),
  fileInput = ref(null);
const trainingTarget = ref('full_time');
const scoped = computed(() => !!payload.value?.competition_workspaces);
const data = computed(() =>
  scoped.value
    ? {
        ...payload.value,
        ...payload.value.competition_workspaces[selectedCompetition.value],
      }
    : payload.value,
);
const competitionOptions = computed(() =>
  (payload.value?.competitions || []).map((c) => ({
    label: c.name,
    value: c.id,
  })),
);
watch(selectedCompetition, (id) => {
  localStorage.setItem("football:model-competition", id);
  drawer.value = null;
  error.value = "";
});
async function selectModel(row) {
  const cid = selectedCompetition.value;
  selecting.value = true;
  error.value = "";
  try {
    await api.selectModel(cid, row.selected ? null : row.run_id);
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    selecting.value = false;
  }
}
async function importDataset(event) {
  const file = event.target.files?.[0],
    cid = selectedCompetition.value;
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) {
    error.value = "数据文件不能超过15MB";
    event.target.value = "";
    return;
  }
  importing.value = true;
  error.value = "";
  try {
    await api.importDataset(cid, JSON.parse(await file.text()));
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    importing.value = false;
    event.target.value = "";
  }
}
const detailOpen = computed({
  get: () => !!drawer.value,
  set: (v) => {
    if (!v) drawer.value = null;
  },
});
const methodNames = { poisson: '攻防泊松', dc: '两阶段低比分修正', joint_dc: '联合 Dixon–Coles', elo_logistic: 'Elo 逻辑回归', catboost: 'CatBoost 胜平负', lightgbm_goals: 'LightGBM 进球', dynamic_gamma: '动态 Gamma–Poisson' };
const marketNames = { had: '胜平负', hhad: '让球', ttg: '总进球', crs: '比分', hafu: '半全场' };
const marketLabel = (markets) => (markets || []).map(k => marketNames[k] || k).join(' / ') || '—';
function showDetails(title, fields) {
  drawer.value = {
    title,
    fields: fields.filter(([, value]) => value != null && value !== ""),
  };
}
function sourceDetails(row) {
  showDetails(row.name, [
    ["覆盖内容", row.scope],
    ["最近状态", statusNames[row.status] || "尚未请求"],
    ["更新时间", timeLabel(row.checked_at)],
    ["最近成功", row.last_success ? timeLabel(row.last_success) : null],
    ["读取场次", row.count],
    ["未匹配", row.unmatched_count],
    ["详情", row.error || row.reason],
  ]);
}
function modelDetails(row) {
  showDetails(row.name, [
    ["版本", row.id],
    ["使用状态", row.status],
    ["方法", row.method],
    ["验证情况", row.validation],
    ["训练数据版本", row.dataset_digest],
    ["训练时间", row.created_at ? timeLabel(row.created_at) : null],
    ['训练截止', row.trained_before ? timeLabel(row.trained_before) : null],
    ['支持玩法', marketLabel(row.supported_markets)],
    ['评估限制', (row.limitations || []).join('\n')],
  ]);
}
function historyDetails(row) {
  showDetails(
    `${leagueName(row.competition_id)} · ${seasonLabel(row.season)}`,
    [
      ["采集场次", row.collected],
      ["含半场比分", row.half_matches],
      ["双源核对", row.verified],
      ["已入训练库", row.imported],
      ["待处理", row.issues?.join("\n")],
      [
        "采集来源授权",
        data.value?.training?.staging?.rights_status === "verified"
          ? "已确认"
          : "待确认",
      ],
    ],
  );
}
const leagueName = (id) => ({ "jp.j1": "日职", "kr.k1": "韩职" })[id] || id;
const seasonLabel = (s) =>
  s.endsWith("-special")
    ? s.replace("-special", " 百年构想")
    : s.replace("-", "/");
async function evaluate() {
  evaluating.value = true;
  error.value = "";
  try {
    await api.evaluate(selectedCompetition.value, trainingTarget.value);
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    evaluating.value = false;
  }
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    payload.value = await api.capabilities();
    if (
      scoped.value &&
      !payload.value.competition_workspaces[selectedCompetition.value]
    )
      selectedCompetition.value = payload.value.competitions[0]?.id;
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
const sources = computed(() => [
  {
    id: "sporttery",
    name: "体彩官方",
    scope: "比赛 / 五种玩法 SP / 赔率历史",
    ...data.value?.sources?.find((s) => s.id === "sporttery"),
  },
  ...(data.value?.sources || [])
    .filter((s) => s.id.startsWith("context:") || s.id === "live-scores")
    .map((s) => ({
      scope: s.id === "live-scores" ? "比分 / 比赛状态" : "近期赛果 / 首发",
      ...s,
    })),
  ...(data.value?.training?.staging?.available
    ? [
        {
          id: "history",
          name: "500 历史赛果",
          scope: scoped.value
            ? `${data.value.competition.name} · 半全场赛果`
            : "日职 / 韩职 · 半全场赛果",
          status:
            data.value.training.staging.status === "collecting"
              ? "collecting"
              : "collected",
          checked_at: data.value.training.staging.collected_at,
          count: collectedRows.value,
          reason:
            (data.value.training.blockers || []).join("\n") ||
            (data.value.training.rows
              ? `已入训练库 ${data.value.training.rows} 场`
              : data.value.training.reason || "待验收数据"),
        },
      ]
    : []),
  ...(data.value?.provider_configuration || []).filter(
    (s) => showUnconfigured.value || s.configured,
  ),
]);
const collectedRows = computed(() =>
  (data.value?.training?.staging?.coverage || []).reduce(
    (n, r) => n + r.collected,
    0,
  ),
);
const modelRows = computed(() =>
  (data.value?.models || []).filter(
    (m) => showRetired.value || m.status !== "已停用",
  ),
);
const statusNames = {
  available: "请求成功",
  partial: "部分覆盖",
  error: "请求失败",
  not_configured: "未配置",
  pending_validation: "待验收",
  collected: "已采集",
  collecting: "采集中",
};
const jobRunning = computed(
  () => data.value?.training?.job?.status === "running",
);
const seasonOrder = (season) =>
  season.endsWith("-special")
    ? season.replace("-special", "-06")
    : /^\d{4}$/.test(season)
      ? `${season}-12`
      : season;
const historyRows = computed(() => {
  const imported = new Map(
    (data.value?.training?.season_coverage || []).map((r) => [r.id, r]),
  );
  const rows = (data.value?.training?.staging?.coverage || []).map((r) => ({
    ...r,
    imported: imported.get(r.id)?.imported || 0,
    status: imported.get(r.id)?.imported
      ? imported.get(r.id).imported === r.collected
        ? "已入库"
        : "部分入库"
      : r.issues?.length
        ? "待核查"
        : r.status === "待入库"
          ? "待验收"
          : r.status,
  }));
  for (const r of imported.values()) {
    if (!rows.some((s) => s.id === r.id))
      rows.push({
        ...r,
        collected: null,
        half_matches: null,
        verified: null,
        status: "已入库",
      });
  }
  return rows.sort(
    (a, b) =>
      seasonOrder(b.season).localeCompare(seasonOrder(a.season)) ||
      a.competition_id.localeCompare(b.competition_id),
  );
});
const competitionRows = computed(() =>
  (data.value?.competitions || [])
    .flatMap((c) =>
      c.seasons.map((s) => ({
        ...s,
        id: `${c.id}:${s.id}`,
        competition_id: c.id,
        name: c.name,
        rule_source: c.rule_source,
      })),
    )
    .sort(
      (a, b) =>
        seasonOrder(b.id.split(":")[1]).localeCompare(
          seasonOrder(a.id.split(":")[1]),
        ) || a.name.localeCompare(b.name),
    ),
);
const historyColumns = [
  { colKey: "competition_id", title: "赛事", width: 90 },
  { colKey: "season", title: "赛季", width: 155 },
  { colKey: "collected", title: "已采集", width: 100 },
  { colKey: "half_matches", title: "含半场比分", width: 110 },
  { colKey: "verified", title: "双源核对", width: 110 },
  { colKey: "imported", title: "已入训练库", width: 110 },
  { colKey: "status", title: "状态", width: 100 },
  { colKey: "action", title: "操作", width: 70 },
];
let timer;
onMounted(() => {
  load();
  timer = setInterval(() => {
    if (
      (jobRunning.value ||
        Object.values(payload.value?.competition_workspaces || {}).some(
          (w) => w.training?.job?.status === "running",
        ) ||
        data.value?.training?.staging?.status === "collecting") &&
      !loading.value
    )
      load();
  }, 3000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>
<template>
  <div class="model-workspace">
    <div class="page-heading">
      <div>
        <h1>数据与模型</h1>
      </div>
      <div class="workspace-actions">
        <t-select
          v-if="scoped"
          v-model="selectedCompetition"
          aria-label="选择赛事"
          :options="competitionOptions"
          class="competition-selector"
        />
        <t-button variant="outline" :loading="loading" @click="load"
          ><template #icon><RefreshIcon /></template>刷新状态</t-button
        >
      </div>
    </div>
    <t-alert v-if="error" theme="error" :message="error" class="mb16" />
    <div v-if="data" class="summary-strip">
      <div>
        <span>已采集历史</span><strong>{{ collectedRows }}</strong>
      </div>
      <div>
        <span>已入训练库</span><strong>{{ data.training?.rows ?? 0 }}</strong>
      </div>
      <div>
        <span>足球模型</span
        ><strong class="model-state">{{
          data.training?.model
            ? "前瞻观察"
            : data.training?.candidate
              ? "待选用"
              : data.competition?.training_enabled === false
                ? "待接入"
                : "待训练"
        }}</strong>
      </div>
    </div>
    <t-loading v-if="loading && !data" text="加载中" />
    <template v-if="data">
      <t-tabs v-model="tab" class="detail-tabs"
        ><t-tab-panel value="sources" label="数据来源" />
        <t-tab-panel v-if="scoped" value="fundamentals" label="基本面" />
        <t-tab-panel v-if="scoped" value="odds" label="赔率" /><t-tab-panel
          value="models"
          label="预测模型" /><t-tab-panel
          value="training"
          label="训练与评估" /><t-tab-panel
          value="competitions"
          label="赛事覆盖"
      /></t-tabs>
      <template v-if="tab === 'sources'"
        ><section class="panel">
          <div class="panel-heading">
            <h2>接入状态</h2>
            <t-checkbox v-model="showUnconfigured">显示未配置</t-checkbox>
          </div>
          <t-table
            row-key="id"
            :data="sources"
            :loading="loading"
            :columns="[
              { colKey: 'name', title: '数据源', width: 170, ellipsis: true },
              { colKey: 'scope', title: '覆盖内容', ellipsis: true },
              { colKey: 'status', title: '状态', width: 120 },
              { colKey: 'checked_at', title: '更新时间', width: 185 },
              { colKey: 'action', title: '操作', width: 70 },
            ]"
            ><template #status="{ row }"
              ><t-tag
                variant="light"
                :theme="
                  row.status === 'available'
                    ? 'success'
                    : row.status === 'error'
                      ? 'danger'
                      : 'default'
                "
                >{{ statusNames[row.status] || "尚未请求" }}</t-tag
              ></template
            ><template #checked_at="{ row }">{{
              timeLabel(row.checked_at)
            }}</template
            ><template #action="{ row }"
              ><t-button
                variant="text"
                theme="primary"
                @click="sourceDetails(row)"
                >详情</t-button
              ></template
            ></t-table
          >
        </section>
        <div class="detail-grid">
          <section class="panel">
            <div class="panel-heading"><h2>当前覆盖</h2></div>
            <dl class="context-list">
              <div>
                <dt>后台采集</dt>
                <dd>
                  {{
                    data?.collector?.running
                      ? `运行中 · ${data.collector.interval_seconds}秒`
                      : "未运行"
                  }}
                </dd>
              </div>
              <div>
                <dt>所选赛事</dt>
                <dd>{{ data?.match_count || 0 }}场已收录比赛；以下数量表示至少取得部分资料</dd>
              </div>
              <div v-for="[key, label] in [['recent_both', '双方近期赛果'], ['venue_both', '主客场表现'], ['confirmed_both', '双方正式阵容'], ['estimated', '预计阵容'], ['injuries', '人员消息'], ['statistics', '比赛统计'], ['events', '关键事件'], ['xg', 'xG'], ['external', '公开欧赔']]" :key="key">
                <dt>{{ label }}</dt>
                <dd>{{ data?.coverage_summary ? `已取得${data.coverage_summary[key] ?? 0}/${data.coverage_summary.total}场` : '等待覆盖核查' }}</dd>
              </div>
              <div><dt>最近资料采集</dt><dd>{{ timeLabel(data?.coverage_summary?.checked_at) }}；逐场新鲜度见比赛详情</dd></div>
            </dl>
          </section>
        </div></template
      >
      <template v-else-if="tab === 'fundamentals'">
        <section class="panel">
          <div class="panel-heading"><h2>基本面数据</h2></div>
          <t-table
            row-key="id"
            :data="data.fundamentals"
            :columns="[
              { colKey: 'name', title: '数据项', ellipsis: true },
              { colKey: 'status', title: '状态', width: 150 },
              { colKey: 'usage', title: '用途', ellipsis: true },
            ]"
          />
        </section>
      </template>
      <template v-else-if="tab === 'odds'">
        <section class="panel">
          <div class="panel-heading"><h2>赔率数据</h2></div>
          <t-table
            row-key="id"
            :data="data.odds"
            :columns="[
              { colKey: 'name', title: '数据项', ellipsis: true },
              { colKey: 'source', title: '来源', width: 150 },
              { colKey: 'status', title: '状态', width: 120 },
              { colKey: 'usage', title: '用途', ellipsis: true },
            ]"
          />
        </section>
      </template>
      <template v-else-if="tab === 'models'"
        ><section class="panel">
          <div class="panel-heading">
            <h2>模型版本</h2>
            <t-checkbox v-if="!scoped" v-model="showRetired"
              >显示停用模型</t-checkbox
            >
          </div>
          <t-table
            row-key="id"
            :data="modelRows"
            :columns="[
              { colKey: 'name', title: '模型', minWidth: 200, ellipsis: true },
              { colKey: 'supported_markets', title: '支持玩法', minWidth: 180, ellipsis: true },
              { colKey: 'status', title: '使用状态', width: 120 },
              {
                colKey: 'validation_status',
                title: '回测结果',
                width: 160,
                ellipsis: true,
              },
              { colKey: 'action', title: '操作', width: scoped ? 190 : 70 },
            ]"
            empty="暂无模型"
            ><template #validation_status="{ row }">{{
              row.validation_status ||
              (row.status === "待训练" ? "未评估" : "待验证")
            }}</template>
            <template #supported_markets="{ row }">{{ marketLabel(row.supported_markets) }}</template>
            <template #action="{ row }"
              ><t-button
                variant="text"
                theme="primary"
                @click="modelDetails(row)"
                >详情</t-button
              >
              <t-button
                v-if="scoped && row.run_id"
                variant="text"
                theme="primary"
                :loading="selecting"
                @click="selectModel(row)"
                >{{ row.selected ? "停止观察" : "选用观察" }}</t-button
              ></template
            >
          </t-table>
        </section>
      </template>
      <template v-else-if="tab === 'competitions'">
        <section class="panel">
          <div class="panel-heading"><h2>赛事与赛季</h2></div>
          <t-table
            row-key="id"
            :data="competitionRows"
            :columns="[
              { colKey: 'name', title: '赛事', width: 100 },
              { colKey: 'label', title: '赛季', ellipsis: true },
              { colKey: 'match_count', title: '已收录比赛', width: 110 },
              { colKey: 'context_count', title: '有基本面', width: 100 },
              { colKey: 'historical_count', title: '历史赛果', width: 100 },
              { colKey: 'training_count', title: '训练样本', width: 100 },
              { colKey: 'action', title: '操作', width: 70 },
            ]"
          >
            <template #action="{ row }"
              ><t-button
                variant="text"
                theme="primary"
                @click="
                  showDetails(row.name, [
                    ['赛事标识', row.competition_id],
                    ['赛季', row.label],
                    ['已收录比赛', row.match_count],
                    ['有基本面', row.context_count],
                    ['历史赛果', row.historical_count],
                    ['训练样本', row.training_count],
                    [
                      '统计口径',
                      '已收录比赛为已同步的体彩场次；基本面为其中具备近期赛果的场次；历史赛果另行采集。',
                    ],
                    ['官方规则', row.rule_source],
                  ])
                "
                >详情</t-button
              ></template
            >
          </t-table>
        </section>
      </template>
      <template v-else
        ><section class="panel">
          <div class="panel-heading">
            <h2>历史训练数据</h2>
            <template v-if="scoped"
              ><input
                ref="fileInput"
                type="file"
                accept=".json,application/json"
                hidden
                @change="importDataset"
              /><t-button
                variant="outline"
                :loading="importing"
                @click="fileInput.click()"
                >导入数据</t-button
              ></template
            >
            <span v-if="data?.training?.staging?.collected_at" class="muted">{{
              timeLabel(data.training.staging.collected_at)
            }}</span>
          </div>
          <t-table
            row-key="id"
            :data="historyRows"
            :columns="historyColumns"
            :scroll="{ x: 900 }"
            empty="尚无历史数据"
          >
            <template #season="{ row }">{{
              row.season.endsWith("-special")
                ? row.season.replace("-special", " 百年构想")
                : row.season.replace("-", "/")
            }}</template>
            <template #competition_id="{ row }">{{
              { "jp.j1": "日职", "kr.k1": "韩职" }[row.competition_id] ||
              row.competition_id
            }}</template>
            <template #collected="{ row }"
              >{{ row.collected ?? "—"
              }}<template v-if="row.expected">
                / {{ row.expected }}</template
              ></template
            >
            <template #half_matches="{ row }">{{
              row.half_matches ?? "—"
            }}</template>
            <template #verified="{ row }">{{ row.verified ?? "—" }}</template>
            <template #action="{ row }"
              ><t-button
                variant="text"
                theme="primary"
                @click="historyDetails(row)"
                >详情</t-button
              ></template
            >
            <template #status="{ row }"
              ><t-tooltip
                v-if="row.issues?.length"
                :content="row.issues.join('；')"
                ><span>{{ row.status }}</span></t-tooltip
              ><span v-else>{{ row.status }}</span></template
            >
          </t-table>
          <div v-if="data?.training?.staging?.available" class="table-footer">
            <span
              >采集来源授权：{{
                data.training.staging.rights_status === "verified"
                  ? "已确认"
                  : "待确认"
              }}</span
            >
            <span v-if="data.training.staging.excluded?.length"
              >暂未纳入：{{
                data.training.staging.excluded
                  .map((r) =>
                    r.season === "2026-special"
                      ? "日职 2026 百年构想"
                      : r.season,
                  )
                  .join("、")
              }}</span
            >
          </div>
        </section>
        <section class="panel">
          <div class="panel-heading">
            <h2>训练与回测</h2>
            <t-select v-model="trainingTarget" aria-label="训练目标" :disabled="evaluating || jobRunning" :style="{ width: '160px' }" :options="[{ label: '全场玩法', value: 'full_time' }, { label: '仅胜平负', value: 'had' }]" />
            <t-button
              :loading="evaluating || jobRunning"
              :disabled="!data?.training?.ready || jobRunning"
              @click="evaluate"
              >训练与回测</t-button
            >
          </div>
          <dl class="context-list">
            <div v-if="data?.training?.job">
              <dt>训练任务</dt>
              <dd>
                {{
                  {
                    running: "运行中",
                    completed: "已完成",
                    failed: "失败",
                    interrupted: "已中断",
                  }[data.training.job.status]
                }}<template v-if="jobRunning">
                  ·
                  {{
                    { validation: "验证", test: "测试", fit: "保存模型" }[
                      data.training.job.phase
                    ]
                  }} · {{ data.training.job.algorithm }}<template v-if="['validation', 'test'].includes(data.training.job.phase)">
                    {{ data.training.job.completed }}/{{
                      data.training.job.total
                    }}</template
                  ></template
                >
              </dd>
            </div>
            <div>
              <dt>训练样本</dt>
              <dd>{{ data?.training?.rows || 0 }} 场</dd>
            </div>
            <div>
              <dt>数据状态</dt>
              <dd>
                {{ data?.training?.reason || "尚无历史数据" }}
                <t-button
                  v-if="!data?.training?.ready"
                  variant="text"
                  theme="primary"
                  @click="
                    showDetails('训练条件', [
                      ['数据状态', data?.training?.reason],
                      ['待处理', (data?.training?.blockers || []).join('\n')],
                    ])
                  "
                  >查看原因</t-button
                >
              </dd>
            </div>
            <div v-if="data?.training?.evaluation">
              <dt>评估数据</dt>
              <dd>
                {{
                  data.training.evaluation.dataset_digest ===
                  data.training.dataset_digest
                    ? "当前版本"
                    : "历史版本"
                }}
              </dd>
            </div>
            <div>
              <dt>评估口径</dt>
              <dd>
                {{
                  [
                    "kickoff_plus_7_days_assumption",
                    "historical_7_day_lag_or_observed",
                  ].includes(data?.training?.availability_policy)
                    ? "赛果延迟7天假设 · 时间切分"
                    : "赛前60分钟 · 时间切分 · 分联赛对比"
                }}
              </dd>
            </div>
            <div v-if="data?.training?.usage_mode === 'local_research'">
              <dt>使用范围</dt>
              <dd>本地实验</dd>
            </div>
            <div>
              <dt>生产模型</dt>
              <dd>市场基线</dd>
            </div>
            <div>
              <dt>足球模型</dt>
              <dd>{{ data?.training?.model ? "前瞻观察" : "待训练" }}</dd>
            </div>
          </dl>
          <t-alert
            v-if="data?.training?.job?.error"
            theme="error"
            :message="data.training.job.error"
            class="mb16"
          />
          <t-table
            v-if="data?.training?.coverage?.length"
            row-key="id"
            :data="data.training.coverage"
            :columns="[
              { colKey: 'id', title: '赛事' },
              { colKey: 'matches', title: '比赛数' },
              { colKey: 'seasons', title: '赛季数' },
              { colKey: 'half_matches', title: '半场比分' },
              { colKey: 'odds_matches', title: '历史赔率' },
            ]"
            ><template #id="{ row }">{{
              { "jp.j1": "日职", "kr.k1": "韩职" }[row.id] || row.id
            }}</template></t-table
          >
          <t-table
            v-if="data?.training?.evaluation"
            row-key="id"
            :data="[
              {
                id: 'model',
                name: '模型（相同样本）',
                ...data.training.evaluation.paired_model,
              },
              {
                id: 'market',
                name: '市场基线',
                ...data.training.evaluation.market_baseline,
              },
            ]"
            :columns="[
              { colKey: 'name', title: '对照' },
              { colKey: 'count', title: '样本数' },
              { colKey: 'log_loss', title: 'Log loss' },
              { colKey: 'brier', title: 'Brier' },
            ]"
          />
          <template v-if="data?.training?.evaluation?.selected_by_competition">
            <div class="panel-heading mt16"><h2>联赛模型</h2></div>
            <t-table
              row-key="id"
              :data="
                Object.entries(
                  data.training.evaluation.selected_by_competition,
                ).map(([id, v]) => ({
                  id,
                  ...v,
                  test: data.training.evaluation.by_competition[id],
                  reference:
                    data.training.evaluation.reference_checks?.[id]?.label ||
                    '—',
                }))
              "
              :columns="[
                { colKey: 'id', title: '赛事', width: 100 },
                { colKey: 'kind', title: '候选方法', minWidth: 150 },
                { colKey: 'mode', title: '训练方式', minWidth: 150 },
                { colKey: 'half_life', title: '衰减天数', width: 100 },
                { colKey: 'test', title: '测试集 Log loss', width: 160 },
                { colKey: 'reference', title: '均匀基线对照', width: 160 },
              ]"
            >
              <template #id="{ row }">{{
                { "jp.j1": "日职", "kr.k1": "韩职" }[row.id] || row.id
              }}</template>
              <template #kind="{ row }">{{
                methodNames[row.kind] || row.kind
              }}</template>
              <template #mode="{ row }">{{
                {
                  separate: "单联赛",
                  pooled: "共享参数",
                  hierarchical: "联赛参数收缩",
                }[row.mode]
              }}</template>
              <template #test="{ row }">{{
                row.test?.log_loss?.toFixed(4) ?? "—"
              }}</template>
            </t-table>
            <div class="panel-heading mt16"><h2>各玩法测试</h2></div>
            <t-table
              row-key="code"
              :data="
                Object.entries(data.training.evaluation.markets || {}).map(
                  ([code, v]) => ({ code, ...v }),
                )
              "
              :columns="[
                { colKey: 'code', title: '玩法' },
                { colKey: 'count', title: '样本数' },
                { colKey: 'log_loss', title: 'Log loss' },
                { colKey: 'brier', title: 'Brier' },
              ]"
            >
              <template #code="{ row }">{{
                {
                  had: "胜平负",
                  hhad: "让球胜平负",
                  ttg: "总进球",
                  crs: "比分",
                  hafu: "半全场",
                }[row.code]
              }}</template>
              <template #log_loss="{ row }">{{
                row.log_loss?.toFixed(4) ?? "—"
              }}</template>
              <template #brier="{ row }">{{
                row.brier?.toFixed(4) ?? "—"
              }}</template>
            </t-table>
          </template>
        </section>
        <section v-if="data?.training?.benchmark" class="panel">
          <div class="panel-heading">
            <h2>模型对比</h2>
            <t-button
              variant="text"
              @click="
                showDetails('评估范围', [
                  ['用途', '历史探索比较 · 未切换观察模型'],
                  ['训练目标', data.training.benchmark.target === 'had' ? '仅胜平负' : '全场玩法'],
                  ['限制', data.training.benchmark.limitations.join('\n')],
                  ['数据版本', data.training.benchmark.dataset_digest],
                  ['特征版本', data.training.benchmark.feature_version],
                  ['缺少数据', data.training.benchmark.data_audit?.missing_features?.join(' / ')],
                  ['完成时间', data.training.benchmark.created_at],
                ])
              "
              >详情</t-button
            >
          </div>
          <t-table
            row-key="key"
            :data="data.training.benchmark.models.map(m => ({ ...m, key: m.key || `${m.kind}:${m.half_life || 180}` }))"
            :columns="[
              { colKey: 'kind', title: '模型', width: 200 },
              { colKey: 'half_life', title: '衰减天数', width: 85 },
              { colKey: 'scope', title: '评估玩法' },
              { colKey: 'samples', title: '测试场次' },
              { colKey: 'validation', title: '验证误差' },
              { colKey: 'loss', title: '测试误差' },
              { colKey: 'brier', title: 'Brier' },
              { colKey: 'selection', title: '验证集选优' },
              { colKey: 'details', title: '操作', width: 70 },
            ]"
          >
            <template #kind="{ row }">{{
              methodNames[row.kind] || row.kind
            }}</template>
            <template #half_life="{ row }">{{ ['elo_logistic', 'catboost', 'lightgbm_goals'].includes(row.kind) ? '—' : row.half_life || 180 }}</template>
            <template #scope>胜平负</template>
            <template #samples="{ row }">{{ row.test.count }}</template>
            <template #validation="{ row }">{{
              row.validation.log_loss.toFixed(4)
            }}</template>
            <template #details="{ row }"><t-button variant="text" theme="primary" @click="showDetails(methodNames[row.kind] || row.kind, [
              ['支持玩法', marketLabel(row.supported_markets)],
              ['目标适配', row.eligible_for_target === false ? '仅作对照，不参与本目标选优' : '可参与选优'],
              ['验证样本', row.validation.count],
              ['各玩法误差', Object.entries(row.markets || {}).map(([code, value]) => `${marketNames[code]}：${value.count || 0} 场 / ${value.log_loss?.toFixed(4) || '—'}`).join('\n')],
              ['限制', data.training.benchmark.limitations.join('\n')],
            ])">详情</t-button></template>
            <template #loss="{ row }">{{
              row.test.log_loss.toFixed(4)
            }}</template>
            <template #brier="{ row }">{{
              row.test.brier.toFixed(4)
            }}</template>
            <template #selection="{ row }">{{
              (data.training.benchmark.selected_key ? data.training.benchmark.selected_key === row.key : data.training.benchmark.selected_on_validation === row.kind)
                ? "入选候选"
                : "—"
            }}</template>
          </t-table>
          <div class="table-footer">
            历史探索 · 误差越低越好 · 市场对照
            {{ data.training.benchmark.market_comparison_count }} 场
          </div>
        </section></template
      >
    </template>
    <t-drawer
      v-model:visible="detailOpen"
      :header="drawer?.title"
      :footer="false"
      size="min(520px, 100vw)"
    >
      <dl class="context-list model-details">
        <div v-for="[label, value] in drawer?.fields || []" :key="label">
          <dt>{{ label }}</dt>
          <dd>
            <a
              v-if="label === '官方规则'"
              :href="value"
              target="_blank"
              rel="noopener noreferrer"
              >查看官方规则 ↗</a
            ><template v-else>{{ value }}</template>
          </dd>
        </div>
      </dl>
    </t-drawer>
  </div>
</template>
<style scoped>
.workspace-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}
.competition-selector {
  width: 160px;
}
.model-workspace {
  min-width: 0;
}
.model-workspace :deep(.t-table td) {
  white-space: nowrap;
}
.model-state {
  font-size: 24px;
}
.model-details dd {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 600px) {
  .workspace-actions {
    gap: 8px;
  }
  .competition-selector {
    width: 110px;
  }
}
@media (min-width: 1000px) {
  .model-workspace :deep(.t-table__content > table) {
    min-width: 0 !important;
  }
}
</style>

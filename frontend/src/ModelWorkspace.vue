<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { RefreshIcon } from "tdesign-icons-vue-next";
import { api, timeLabel } from "./api";
import WorkspaceTable from "./model-workspace/WorkspaceTable.vue";
import {
  tabs,
  methods,
  workspaceData,
  taskBody,
  reportFields,
  shortVersion,
} from "./model-workspace/view-model";
const tab = ref("overview"),
  payload = ref(null),
  loading = ref(false),
  busy = ref(false),
  error = ref(""),
  drawer = ref(null),
  fileInput = ref(null),
  taskOpen = ref(false);
const selectedCompetition = ref(
  localStorage.getItem("football:model-competition") || "jp.j1",
);
const data = computed(() =>
  workspaceData(payload.value, selectedCompetition.value),
);
const training = computed(() => data.value?.training || {});
const options = computed(() =>
  (payload.value?.competitions || []).map((c) => ({
    value: c.id,
    label: c.name,
  })),
);
const overview = computed(() => {
  const value = data.value?.overview || {};
  return {
    ...value,
    matches: (value.matches || []).map((m) => ({
      ...m,
      reason:
        m.reason ||
        (m.reasons || [])
          .map((r) =>
            typeof r === "string" ? r : r.message || r.reason || r.code,
          )
          .join("；"),
    })),
  };
});
const teams = computed(() =>
  Array.isArray(data.value?.teams)
    ? data.value.teams
    : data.value?.teams?.rows || [],
);
const datasets = computed(() => data.value?.datasets || {});
const versions = computed(() =>
  Array.isArray(datasets.value)
    ? datasets.value
    : datasets.value.versions || [],
);
const seasons = computed(
  () =>
    datasets.value.season_coverage ||
    training.value.season_coverage ||
    training.value.staging?.coverage ||
    [],
);
const issues = computed(() => data.value?.issues || []);
const visibleFundamentals = computed(() =>
  (data.value?.fundamentals || []).filter(
    (row) =>
      ["results", "halves"].includes(row.id) ||
      (row.id !== "cross" && /已(?:取得|核查)[1-9]/.test(row.status || "")),
  ),
);
const coverageGaps = computed(() =>
  (data.value?.fundamentals || []).filter(
    (row) => !visibleFundamentals.value.includes(row),
  ),
);
function showCoverageGaps() {
  drawer.value = {
    title: "覆盖缺口",
    fields: coverageGaps.value.map((row) => [
      row.name,
      { 状态: row.status, 详情: row.usage },
    ]),
  };
}
const tasks = computed(() => data.value?.tasks || []);
const models = computed(() =>
  (data.value?.models || []).filter((m) => m.run_id),
);
const current = computed(() => training.value.model || null);
const currentName = computed(
  () =>
    models.value.find((row) => row.run_id === current.value?.run_id)?.name ||
    methods.find((row) => row.value === current.value?.kind)?.label ||
    (current.value ? "足球模型" : "未选用"),
);
const currentSamples = computed(
  () =>
    versions.value.find((row) => row.digest === current.value?.dataset_digest)
      ?.rows,
);
const marketLabels = {
  had: "胜平负",
  hhad: "让球胜平负",
  crs: "比分",
  ttg: "总进球",
  hafu: "半全场",
};
const currentMarkets = computed(
  () =>
    (current.value?.supported_markets || [])
      .map((k) => marketLabels[k] || k)
      .join(" / ") || "—",
);
const digest = computed(
  () => datasets.value.current_digest || training.value.dataset_digest,
);
const taskForm = ref({
  kind: "fit",
  target: "full_time",
  algorithm: "poisson",
  half_life: 180,
  run_id: "",
  date_from: "",
  date_to: "",
  dataset_digest: "",
});
watch(
  () => taskForm.value.target,
  (target) => {
    if (
      target === "full_time" &&
      ["elo_logistic", "catboost"].includes(taskForm.value.algorithm)
    )
      taskForm.value.algorithm = "poisson";
  },
);
const detailVisible = computed({
  get: () => !!drawer.value,
  set: (v) => {
    if (!v) drawer.value = null;
  },
});
const format = (v) =>
  typeof v === "object" ? JSON.stringify(v, null, 2) : String(v ?? "—");
let sequence = 0,
  reportSequence = 0,
  timer;
async function load() {
  const id = selectedCompetition.value,
    request = ++sequence;
  loading.value = true;
  error.value = "";
  try {
    const result = await api.workspace(id);
    if (request === sequence && id === selectedCompetition.value)
      payload.value = result;
  } catch (e) {
    if (request === sequence) error.value = e.message;
  } finally {
    if (request === sequence) loading.value = false;
  }
}
watch(selectedCompetition, () => {
  localStorage.setItem("football:model-competition", selectedCompetition.value);
  payload.value = null;
  drawer.value = null;
  taskOpen.value = false;
  ++reportSequence;
  load();
});
function details(title, row) {
  drawer.value = {
    title,
    fields: Object.entries(row).map(([k, v]) => [fieldNames[k] || k, v]),
  };
}
const fieldNames = {
  id: "标识",
  name: "名称",
  status: "状态",
  reason: "原因",
  error: "错误",
  source: "来源",
  source_ids: "来源映射",
  season: "赛季",
  competition_id: "赛事",
  dataset_digest: "数据版本",
  run_id: "运行版本",
  created_at: "创建时间",
  checked_at: "核查时间",
  scope: "覆盖范围",
  count: "数量",
  issues: "问题",
  limitations: "限制",
  kind: "任务类型",
  parent_id: "父任务",
  protocol: "评估协议",
  mapping_status: "映射状态",
};
async function report(row) {
  const id = selectedCompetition.value,
    n = ++reportSequence;
  busy.value = true;
  error.value = "";
  try {
    const result = await api.runReport(id, row.run_id);
    if (id === selectedCompetition.value && n === reportSequence)
      drawer.value = {
        title: "模型报告",
        fields: reportFields(result, row.run_id),
      };
  } catch (e) {
    if (id === selectedCompetition.value) error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function select(row) {
  const id = selectedCompetition.value;
  busy.value = true;
  error.value = "";
  try {
    await api.selectObservation(
      id,
      row.run_id === current.value?.run_id ? null : row.run_id,
      data.value?.selection_revision ??
        training.value.selection_revision ??
        current.value?.selection_revision ??
        0,
    );
    if (id === selectedCompetition.value) await load();
  } catch (e) {
    if (id === selectedCompetition.value) error.value = e.message;
  } finally {
    busy.value = false;
  }
}
function newTask() {
  taskForm.value = {
    kind: "fit",
    target: "full_time",
    algorithm: "poisson",
    half_life: 180,
    run_id: "",
    date_from: "",
    date_to: "",
    dataset_digest: digest.value || "",
  };
  taskOpen.value = true;
}
async function submitTask() {
  const id = selectedCompetition.value;
  busy.value = true;
  error.value = "";
  try {
    await api.createTrainingTask(
      id,
      taskBody(taskForm.value, taskForm.value.dataset_digest),
    );
    if (id === selectedCompetition.value) {
      taskOpen.value = false;
      await load();
    }
  } catch (e) {
    if (id === selectedCompetition.value) error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function ingestHistory() {
  const id = selectedCompetition.value;
  busy.value = true;
  error.value = "";
  try {
    await api.ingestHistory(id, crypto.randomUUID());
    if (id === selectedCompetition.value) await load();
  } catch (e) {
    if (id === selectedCompetition.value) error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function importData(event) {
  const file = event.target.files?.[0],
    id = selectedCompetition.value;
  if (!file) return;
  busy.value = true;
  error.value = "";
  try {
    if (file.size > 15 * 1024 * 1024) throw new Error("数据文件不能超过15MB");
    await api.importDataset(id, JSON.parse(await file.text()));
    if (id === selectedCompetition.value) await load();
  } catch (e) {
    if (id === selectedCompetition.value) error.value = e.message;
  } finally {
    busy.value = false;
    event.target.value = "";
  }
}
onMounted(() => {
  load();
  timer = setInterval(() => {
    if (
      !loading.value &&
      [...tasks.value, ...(data.value?.ingestion_tasks || [])].some((t) =>
        ["running", "queued"].includes(t.status),
      )
    )
      load();
  }, 5000);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  ++sequence;
  ++reportSequence;
});
</script>
<template>
  <div class="model-workspace">
    <div class="page-heading">
      <h1>数据与模型</h1>
      <div class="workspace-actions">
        <t-select
          v-model="selectedCompetition"
          aria-label="选择赛事"
          :options="options"
          class="competition-selector"
        /><t-button variant="outline" :loading="loading" @click="load"
          ><template #icon><RefreshIcon /></template>刷新状态</t-button
        >
      </div>
    </div>
    <t-alert v-if="error" theme="error" :message="error" class="mb16" />
    <div
      v-if="loading && !data"
      class="workspace-loading"
      role="status"
      aria-live="polite"
    >
      <svg
        class="workspace-loading__spinner"
        viewBox="0 0 32 32"
        aria-hidden="true"
      >
        <circle class="workspace-loading__track" cx="16" cy="16" r="13" />
        <path d="M16 3a13 13 0 0 1 13 13" />
      </svg>
      <span>加载中</span>
    </div>
    <template v-if="data">
      <t-tabs v-model="tab" class="detail-tabs"
        ><t-tab-panel
          v-for="[value, label] in tabs"
          :key="value"
          :value="value"
          :label="label"
      /></t-tabs>
      <template v-if="tab === 'overview'">
        <div class="scope-line">
          <span>{{ overview.date || "日期待核实" }}</span
          ><span>{{ overview.scope || "已收录比赛" }}</span
          ><span>统计时间 {{ timeLabel(overview.checked_at) }}</span>
        </div>
        <div class="summary-strip">
          <div>
            <span>未开赛比赛</span
            ><strong>{{ overview.total ?? data.match_count ?? "—" }}</strong>
          </div>
          <div>
            <span>可预测</span><strong>{{ overview.eligible ?? "—" }}</strong>
          </div>
          <div>
            <span>待处理</span><strong>{{ overview.blocked ?? "—" }}</strong>
          </div>
          <div>
            <span>待核实</span><strong>{{ overview.unknown ?? "—" }}</strong>
          </div>
        </div>
        <section class="panel">
          <div class="panel-heading"><h2>比赛资格</h2></div>
          <WorkspaceTable
            :rows="overview.matches || []"
            :columns="[
              ['name', '比赛', 260],
              ['kickoff', '开球时间', 180],
              ['status', '状态', 120],
              ['reason', '待处理', 280],
              ['action', '操作', 80],
            ]"
            @detail="details('比赛资格', $event)"
          />
        </section>
        <section class="panel">
          <div class="panel-heading">
            <h2>当前使用</h2>
            <t-button variant="text" theme="primary" @click="tab = 'models'"
              >管理模型</t-button
            >
          </div>
          <dl class="context-list">
            <div>
              <dt>观察模型</dt>
              <dd>{{ currentName }}</dd>
            </div>
            <div>
              <dt>数据版本</dt>
              <dd>{{ digest ? shortVersion(digest) : "未入库" }}</dd>
            </div>
          </dl>
        </section>
      </template>
      <template v-else-if="tab === 'teams'"
        ><section class="panel">
          <div class="panel-heading">
            <h2>球队与参赛</h2>
            <span>{{ teams.length }} 条参赛记录</span>
          </div>
          <WorkspaceTable
            :rows="teams"
            :columns="[
              ['name', '球队', 220],
              ['season', '赛季', 150],
              ['status', '参赛状态', 140],
              ['source_ids', '来源映射', 140],
              ['action', '操作', 80],
            ]"
            @detail="details('球队详情', $event)"
          /></section
      ></template>
      <template v-else-if="tab === 'sources'">
        <section class="panel">
          <div class="panel-heading">
            <h2>数据源</h2>
            <t-button
              v-if="data.ingestion_supported"
              :loading="busy"
              :disabled="
                (data.ingestion_tasks || []).some((t) =>
                  ['running', 'queued'].includes(t.status),
                )
              "
              @click="ingestHistory"
              >采集历史</t-button
            >
          </div>
          <WorkspaceTable
            :rows="data.sources || []"
            :columns="[
              ['name', '数据源', 200],
              ['scope', '覆盖范围', 260],
              ['status', '请求状态', 130],
              ['checked_at', '核查时间', 190],
              ['action', '操作', 80],
            ]"
            @detail="details('来源详情', $event)"
          />
        </section>
        <section class="panel">
          <div class="panel-heading"><h2>采集任务</h2></div>
          <WorkspaceTable
            :rows="data.ingestion_tasks || []"
            :columns="[
              ['id', '任务', 240],
              ['status', '状态', 140],
              ['created_at', '创建时间', 190],
              ['action', '操作', 80],
            ]"
            @detail="details('采集任务', $event)"
          />
        </section>
        <section class="panel">
          <div class="panel-heading">
            <h2>基本面覆盖</h2>
            <div class="workspace-actions">
              <span>{{ data.coverage_summary?.total ?? "—" }} 场</span
              ><t-button
                v-if="coverageGaps.length"
                variant="text"
                theme="primary"
                @click="showCoverageGaps"
                >覆盖缺口（{{ coverageGaps.length }}）</t-button
              >
            </div>
          </div>
          <WorkspaceTable
            :rows="visibleFundamentals"
            :columns="[
              ['name', '数据项', 240],
              ['status', '覆盖状态', 200],
              ['action', '操作', 80],
            ]"
            @detail="details('基本面详情', $event)"
          />
        </section>
        <section class="panel">
          <div class="panel-heading"><h2>赔率覆盖</h2></div>
          <WorkspaceTable
            :rows="data.odds || []"
            :columns="[
              ['name', '数据项', 240],
              ['status', '覆盖状态', 200],
              ['action', '操作', 80],
            ]"
            @detail="details('赔率详情', $event)"
          />
        </section>
      </template>
      <template v-else-if="tab === 'datasets'">
        <section class="panel">
          <div class="panel-heading">
            <h2>数据版本</h2>
            <t-button
              variant="outline"
              :loading="busy"
              @click="fileInput.click()"
              >导入数据</t-button
            ><input
              ref="fileInput"
              type="file"
              accept=".json,application/json"
              hidden
              @change="importData"
            />
          </div>
          <WorkspaceTable
            :rows="versions"
            :columns="[
              ['digest', '版本', 250],
              ['rows', '样本', 100],
              ['created_at', '生成时间', 190],
              ['status', '状态', 120],
              ['action', '操作', 80],
            ]"
            @detail="details('数据版本', $event)"
          />
        </section>
        <section class="panel">
          <div class="panel-heading"><h2>赛季对账</h2></div>
          <WorkspaceTable
            :rows="seasons"
            :columns="[
              ['season', '赛季', 180],
              ['collected', '采集', 100],
              ['imported', '纳入', 100],
              ['not_imported', '未纳入', 100],
              ['half_matches', '半场比分', 110],
              ['verified', '双源核对', 110],
              ['action', '操作', 80],
            ]"
            @detail="details('赛季详情', $event)"
          />
        </section>
        <section class="panel">
          <div class="panel-heading"><h2>质量问题</h2></div>
          <WorkspaceTable
            :rows="issues"
            :columns="[
              ['name', '对象', 240],
              ['reason', '问题', 320],
              ['status', '状态', 120],
              ['action', '操作', 80],
            ]"
            @detail="details('问题证据', $event)"
          />
        </section>
      </template>
      <template v-else-if="tab === 'training'">
        <section class="panel">
          <div class="panel-heading">
            <h2>任务记录</h2>
            <t-button
              :disabled="
                !digest || data.competition?.training_enabled === false
              "
              @click="newTask"
              >新建任务</t-button
            >
          </div>
          <WorkspaceTable
            :rows="tasks"
            :columns="[
              ['id', '任务', 230],
              ['kind', '类型', 160],
              ['status', '状态', 100],
              ['created_at', '创建时间', 190],
              ['action', '操作', 80],
            ]"
            @detail="details('任务详情', $event)"
          />
        </section>
      </template>
      <template v-else-if="tab === 'models'">
        <section class="panel">
          <div class="panel-heading">
            <h2>当前使用</h2>
            <t-button
              v-if="current"
              variant="text"
              theme="primary"
              :loading="busy"
              @click="report(current)"
              >查看报告</t-button
            >
          </div>
          <dl class="context-list">
            <div>
              <dt>模型</dt>
              <dd>{{ currentName }}</dd>
            </div>
            <div>
              <dt>选用状态</dt>
              <dd>{{ current ? "前瞻观察" : "未选用" }}</dd>
            </div>
            <div>
              <dt>数据版本</dt>
              <dd>{{ shortVersion(current?.dataset_digest) }}</dd>
            </div>
            <div>
              <dt>训练时间</dt>
              <dd>
                {{ current?.created_at ? timeLabel(current.created_at) : "—" }}
              </dd>
            </div>
            <div>
              <dt>数据样本</dt>
              <dd>{{ currentSamples ?? "—" }}</dd>
            </div>
            <div>
              <dt>支持玩法</dt>
              <dd>{{ currentMarkets }}</dd>
            </div>
            <div>
              <dt>运行版本</dt>
              <dd>{{ shortVersion(current?.run_id) }}</dd>
            </div>
          </dl>
        </section>
        <section class="panel">
          <div class="panel-heading"><h2>模型版本</h2></div>
          <WorkspaceTable
            :rows="models"
            :columns="[
              ['name', '模型', 180],
              ['run_id', '版本', 120],
              ['status', '状态', 120],
              ['created_at', '训练时间', 190],
              ['actions', '操作', 250],
            ]"
            ><template #actions="{ row }"
              ><t-button
                variant="text"
                theme="primary"
                :disabled="busy"
                @click="report(row)"
                >报告</t-button
              ><t-button
                variant="text"
                theme="primary"
                :disabled="busy || row.selectable === false"
                @click="select(row)"
                >{{
                  row.run_id === current?.run_id ? "停止观察" : "选用观察"
                }}</t-button
              ><t-button
                variant="text"
                theme="primary"
                @click="details('模型详情', row)"
                >详情</t-button
              ></template
            ></WorkspaceTable
          >
        </section>
      </template>
    </template>
    <t-drawer
      v-model:visible="detailVisible"
      :header="drawer?.title"
      :footer="false"
      size="min(640px,100vw)"
      ><dl class="context-list model-details">
        <div v-for="[label, value] in drawer?.fields || []" :key="label">
          <dt>{{ label }}</dt>
          <dd>{{ format(value) }}</dd>
        </div>
      </dl></t-drawer
    >
    <t-drawer
      v-model:visible="taskOpen"
      header="新建任务"
      size="min(520px,100vw)"
      :confirm-btn="{
        content: taskForm.kind === 'experiment' ? '运行实验' : '创建任务',
        loading: busy,
      }"
      @confirm="submitTask"
    >
      <t-alert v-if="error" theme="error" :message="error" class="mb16" />
      <div class="task-form">
        <label
          >任务类型<t-select
            v-model="taskForm.kind"
            aria-label="任务类型"
            :options="[
              { value: 'fit', label: '单算法拟合' },
              { value: 'evaluate', label: '固定模型评估' },
              { value: 'experiment', label: '滚动实验' },
            ]"
        /></label>
        <label
          >目标<t-select
            v-model="taskForm.target"
            aria-label="预测目标"
            :options="[
              { value: 'full_time', label: '全场进球' },
              { value: 'had', label: '胜平负' },
            ]"
        /></label>
        <label
          >数据版本<t-input
            v-model="taskForm.dataset_digest"
            aria-label="数据版本"
        /></label>
        <template v-if="taskForm.kind === 'evaluate'"
          ><label
            >模型版本<t-select
              v-model="taskForm.run_id"
              aria-label="评估模型"
              :options="
                models.map((m) => ({ value: m.run_id, label: m.run_id }))
              " /></label
          ><label
            >开始日期<input
              v-model="taskForm.date_from"
              aria-label="开始日期"
              type="date" /></label
          ><label
            >结束日期<input
              v-model="taskForm.date_to"
              aria-label="结束日期"
              type="date" /></label
        ></template>
        <template v-else-if="taskForm.kind === 'fit'"
          ><label
            >算法<t-select
              v-model="taskForm.algorithm"
              aria-label="算法"
              :options="
                methods.filter(
                  (m) =>
                    taskForm.target === 'had' ||
                    !['elo_logistic', 'catboost'].includes(m.value),
                )
              " /></label
          ><label
            >衰减天数<t-select
              v-model="taskForm.half_life"
              aria-label="衰减天数"
              :options="[
                { value: 180, label: '180 天' },
                { value: 365, label: '365 天' },
              ]" /></label
        ></template>
        <dl v-if="taskForm.kind === 'experiment'" class="context-list">
          <div>
            <dt>比较配置</dt>
            <dd>
              泊松、低比分修正：180 / 365
              天；联合修正、Elo、CatBoost、LightGBM、Gamma–Poisson：180 天
            </dd>
          </div>
          <div>
            <dt>执行阶段</dt>
            <dd>验证选参 → 测试评估 → 最终拟合</dd>
          </div>
          <div>
            <dt>模型选用</dt>
            <dd>手动选用</dd>
          </div>
        </dl>
      </div>
    </t-drawer>
  </div>
</template>
<style scoped>
.model-workspace {
  min-width: 0;
}
.workspace-loading {
  min-height: 320px;
  min-height: max(320px, calc(100dvh - 260px));
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--td-text-color-secondary);
}
.workspace-loading__spinner {
  width: 32px;
  height: 32px;
  fill: none;
  stroke: var(--td-brand-color);
  stroke-width: 3;
  stroke-linecap: round;
  animation: workspace-loading-spin 0.8s linear infinite;
}
.workspace-loading__track {
  stroke: var(--td-brand-color-light);
}
@keyframes workspace-loading-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .workspace-loading__spinner {
    animation: none;
  }
}
.workspace-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.competition-selector {
  width: 160px;
}
.scope-line {
  display: flex;
  gap: 24px;
  color: var(--td-text-color-secondary);
  margin: 20px 0;
}
.model-workspace .panel {
  margin-top: 24px;
}
.model-workspace :deep(.t-table td) {
  white-space: nowrap;
}
.model-workspace :deep(.t-table__content) {
  overflow: auto;
}
.model-details dd {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.task-form {
  display: grid;
  gap: 24px;
}
.task-form label {
  display: grid;
  gap: 8px;
}
.task-form input[type="date"] {
  height: 32px;
  border: 1px solid #dcdcdc;
  padding: 0 8px;
}
.context-list dd {
  overflow-wrap: anywhere;
}
.summary-strip {
  margin-bottom: 24px;
  grid-template-columns: repeat(4, 1fr);
}
@media (max-width: 700px) {
  .workspace-actions {
    gap: 8px;
  }
  .competition-selector {
    width: 110px;
  }
  .scope-line {
    flex-wrap: wrap;
    gap: 8px;
  }
  .summary-strip {
    grid-template-columns: repeat(2, 1fr);
  }
  .model-workspace :deep(.t-tabs__nav) {
    overflow: auto;
  }
}
</style>

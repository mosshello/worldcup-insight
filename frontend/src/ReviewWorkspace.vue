<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import LiveScore from "./LiveScore.vue";
import { MessagePlugin } from "tdesign-vue-next";
import { RefreshIcon, SearchIcon } from "tdesign-icons-vue-next";
import { api, timeLabel } from "./api";
import { toCsv } from "./domain";
const props = defineProps({ state: Object });
const emit = defineEmits(["saved", "download"]);
const aiConfig = ref(null),
  generating = ref(false);
const aiLabels = {
  pending: "待生成",
  queued: "排队中",
  running: "生成中",
  completed: "已完成",
  failed: "失败",
  waiting_result: "待赛果",
  not_configured: "未配置",
  disabled: "未开启",
};
const rows = ref([]),
  loading = ref(false),
  error = ref(""),
  query = ref(""),
  status = ref(""),
  selected = ref(null),
  draft = ref(""),
  open = ref(false),
  page = ref(1),
  saving = ref(false);
const list = computed(() =>
  rows.value
    .map((r) => ({
      ...r,
      status:
        props.state.reviewNotes[r.id] || r.ai_review?.status === "completed"
          ? "已复盘"
          : r.status,
    }))
    .filter(
      (r) =>
        (!status.value || r.status === status.value) &&
        `${r.home}${r.away}`.includes(query.value.trim()),
    ),
);
const pageRows = computed(() =>
  list.value.slice((page.value - 1) * 10, page.value * 10),
);
async function load(refresh = false) {
  loading.value = true;
  error.value = "";
  try {
    const d = await (refresh ? api.refreshReviews() : api.reviews());
    rows.value = d.reviews;
    aiConfig.value = d.ai_review_config;
    if (selected.value)
      selected.value =
        rows.value.find((r) => r.id === selected.value.id) || selected.value;
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
function inspect(row) {
  selected.value = row;
  draft.value = props.state.reviewNotes[row.id] || "";
  open.value = true;
}
const marketNames = {
  had: "胜平负",
  hhad: "让球胜平负",
  ttg: "总进球",
  crs: "比分",
  hafu: "半全场",
};
function evidenceTitle(id) {
  const evidence = selected.value?.ai_review?.evidence?.find(
    (e) => e.id === id,
  );
  if (id === "official-result") return "体彩正式赛果";
  if (id === "pregame-model") return "赛前模型记录";
  if (id.startsWith("market:"))
    return "市场参考 · " + marketNames[id.split(":")[1]];
  if (id.startsWith("football:"))
    return "足球模型 · " + marketNames[id.split(":")[1]];
  return evidence?.data?.title || "赛前近期表现";
}
function evidenceText(item) {
  const d = item.data;
  if (item.kind === "official_result") return d.score + "（90分钟）";
  if (item.kind === "computed_settlement")
    return `选择：${d.selection}；赛果：${d.actual || "待确认"}；${d.status}`;
  if (item.kind === "archived_claim_not_match_event") return d.text;
  if (item.kind === "archived_context")
    return d
      .map((r) => `${r.date || ""} ${r.opponent || ""} ${r.score || ""}`)
      .join("；");
  if (item.kind === "archived_prediction")
    return `${d.model_version} · ${timeLabel(d.created)} · 主/平/客：${(d.probabilities || []).join(" / ")}`;
  return "";
}
async function generateReview() {
  generating.value = true;
  try {
    await api.generateReview(selected.value.id);
    await load();
  } catch (e) {
    MessagePlugin.error(e.message);
  } finally {
    generating.value = false;
  }
}
async function save() {
  saving.value = true;
  try {
    await api.save("reviewNotes", selected.value.id, draft.value);
    emit("saved");
    MessagePlugin.success("复盘笔记已保存至数据库");
    open.value = false;
  } catch (e) {
    MessagePlugin.error(e.message);
  } finally {
    saving.value = false;
  }
}
function exportCsv() {
  emit(
    "download",
    toCsv(
      list.value.map((r) => ({
        ...r,
        fixture: `${r.home} vs ${r.away}`,
        version: r.prediction.version,
        note: props.state.reviewNotes[r.id] || "",
      })),
      [
        { title: "开赛日期", key: "date" },
        { title: "比赛", key: "fixture" },
        { title: "赛前方向", key: "pick" },
        { title: "实际比分", key: "score" },
        { title: "对照版本", key: "version" },
        { title: "复盘状态", key: "status" },
        { title: "复盘笔记", key: "note" },
      ],
    ),
    "复盘记录.csv",
  );
}
const columns = [
  { colKey: "date", title: "开赛日期", width: 120 },
  { colKey: "league", title: "赛事", width: 90 },
  { colKey: "fixture", title: "比赛", minWidth: 230 },
  { colKey: "pick", title: "赛前方向", width: 110 },
  { colKey: "liveScore", title: "即时比分", width: 200 },
  { colKey: "score", title: "实际比分", width: 110 },
  { colKey: "result", title: "方向对照", width: 110 },
  { colKey: "status", title: "复盘状态", width: 100 },
  { colKey: "ai", title: "AI复盘", width: 100 },
  { colKey: "action", title: "操作", width: 100 },
];
let timer;
onMounted(() => {
  load(true);
  let ticks = 0;
  timer = setInterval(() => {
    ticks++;
    const active = rows.value.some((r) =>
      ["queued", "running"].includes(r.ai_review?.status),
    );
    if (!document.hidden && !loading.value && (active || ticks % 10 === 0))
      load();
  }, 3000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>复盘记录</h1>
    </div>
    <div class="actions">
      <t-tag v-if="aiConfig?.enabled && aiConfig?.configured" variant="light"
        >自动复盘已开启</t-tag
      >
      <a class="score-page-link" href="#/scores">实时比分</a>
      <t-button variant="outline" :loading="loading" @click="load(true)"
        ><template #icon><RefreshIcon /></template>更新赛果</t-button
      ><t-button variant="outline" :disabled="!list.length" @click="exportCsv"
        >导出记录</t-button
      >
    </div>
  </div>
  <div class="summary-strip">
    <div>
      <span>预测场次</span><strong>{{ rows.length }}</strong>
    </div>
    <div>
      <span>已确认赛果</span
      ><strong>{{ rows.filter((r) => r.status === "待复盘").length }}</strong>
    </div>
    <div>
      <span>已完成复盘</span
      ><strong>{{
        rows.filter(
          (r) => state.reviewNotes[r.id] || r.ai_review?.status === "completed",
        ).length
      }}</strong>
    </div>
  </div>
  <section class="panel">
    <div class="panel-heading">
      <div class="actions">
        <h2>预测与赛果</h2>
        <t-select
          v-model="status"
          clearable
          placeholder="全部状态"
          aria-label="复盘状态"
          style="width: 130px"
          :options="
            ['待赛果', '待复盘', '已复盘', '已取消'].map((v) => ({
              label: v,
              value: v,
            }))
          "
          @change="page = 1"
        />
      </div>
      <t-input
        v-model="query"
        clearable
        placeholder="搜索球队"
        aria-label="搜索复盘球队"
        style="width: 220px"
        @change="page = 1"
        ><template #prefix-icon><SearchIcon /></template
      ></t-input>
    </div>
    <t-alert v-if="error" theme="error" :message="error" /><t-table
      row-key="id"
      :data="pageRows"
      :columns="columns"
      :loading="loading"
      hover
      empty="暂无预测归档。先进入比赛详情生成赛前预测。"
      ><template #fixture="{ row }"
        ><strong>{{ row.home }}</strong
        ><span class="versus">vs</span><strong>{{ row.away }}</strong></template
      ><template #liveScore="{ row }"
        ><LiveScore :value="row.live_score" compact /></template
      ><template #score="{ row }"
        ><strong class="mono">{{ row.score }}</strong></template
      ><template #result="{ row }"
        ><span
          :class="
            row.consistent === true
              ? 'success'
              : row.consistent === false
                ? 'warning'
                : 'muted'
          "
          >{{
            row.consistent === true
              ? "方向一致"
              : row.consistent === false
                ? "出现偏差"
                : row.direction_observed
                  ? "观望"
                  : "待核实"
          }}</span
        ></template
      ><template #status="{ row }"
        ><t-tag
          variant="light"
          :theme="row.status === '待复盘' ? 'warning' : 'default'"
          >{{ row.status }}</t-tag
        ></template
      ><template #ai="{ row }">{{
        aiLabels[row.ai_review?.status] || "—"
      }}</template
      ><template #action="{ row }"
        ><t-button theme="primary" variant="text" @click="inspect(row)"
          >查看复盘</t-button
        ></template
      ></t-table
    >
    <div class="table-footer">
      <span class="small muted">赛果未核实前不计命中 · 历史预测只读</span
      ><t-pagination
        v-model="page"
        :page-size="10"
        :total="list.length"
        :show-page-size="false"
        :show-jumper="false"
        size="small"
      />
    </div>
  </section>
  <t-drawer
    v-model:visible="open"
    header="比赛复盘"
    size="min(820px, 100vw)"
    :close-on-esc-keydown="true"
    :close-on-overlay-click="false"
    :confirm-btn="{
      content: '保存笔记',
      loading: saving,
      disabled: draft === (state.reviewNotes[selected?.id] || ''),
    }"
    cancel-btn="关闭"
    @confirm="save"
    @close="open = false"
    @cancel="open = false"
    ><template v-if="selected"
      ><h2>
        {{ selected.home }} <span class="versus">vs</span> {{ selected.away }}
      </h2>
      <p class="muted">{{ selected.date }} · {{ selected.league }}</p>
      <div class="review-comparison">
        <div>
          <span class="label-caption"
            >赛前方向 · v{{ selected.prediction.version }}</span
          ><strong>{{ selected.pick }}</strong>
        </div>
        <div>
          <span class="label-caption">实际比分</span
          ><strong>{{ selected.score }}</strong>
        </div>
      </div>
      <h3>赛前判断 · 只读</h3>
      <p class="review-text">
        {{ selected.prediction.title }}<br />{{
          selected.prediction.evidence.map((e) => e.text).join("\n")
        }}
      </p>
      <p class="small muted">
        生成于 {{ timeLabel(selected.prediction.created) }} ·
        {{ selected.prediction.model_version }}
      </p>
      <section
        v-for="group in [
          { label: '市场观察复盘', rows: selected.market_results },
          { label: '足球模型复盘', rows: selected.model_results },
        ].filter((g) => g.rows?.length)"
        :key="group.label"
        class="mt16"
      >
        <h3>{{ group.label }}</h3>
        <t-table
          row-key="code"
          :data="group.rows"
          size="small"
          :columns="[
            { colKey: 'label', title: '玩法', width: 100 },
            { colKey: 'selection', title: '赛前选择', minWidth: 150 },
            { colKey: 'actual', title: '赛果', width: 90 },
            { colKey: 'status', title: '对照', width: 100 },
            { colKey: 'unit_cost', title: '模拟投入', width: 90 },
            { colKey: 'unit_profit', title: '模拟盈亏', width: 90 },
          ]"
        >
          <template #unit_cost="{ row }">{{ row.unit_cost ?? "—" }}</template>
          <template #unit_profit="{ row }">{{
            row.unit_profit == null ? "—" : row.unit_profit.toFixed(2)
          }}</template>
        </t-table>
        <p class="small muted">每选项1单位；未归档选择不计覆盖。</p>
      </section>
      <h3 class="mt16">版本记录</h3>
      <t-table
        row-key="id"
        :data="selected.versions"
        size="small"
        :columns="[
          { colKey: 'version', title: '版本', width: 70 },
          { colKey: 'created', title: '生成时间', width: 180 },
          { colKey: 'direction', title: '方向', width: 80 },
          { colKey: 'model_version', title: '计算版本' },
        ]"
        ><template #created="{ row }">{{
          timeLabel(row.created)
        }}</template></t-table
      >
      <section v-if="selected.ai_review || aiConfig" class="ai-review mt16">
        <div class="panel-heading flush">
          <h3>AI复盘</h3>
          <t-button
            v-if="['pending', 'failed'].includes(selected.ai_review?.status)"
            variant="outline"
            :loading="generating"
            @click="generateReview"
            >{{
              selected.ai_review?.status === "failed" ? "重试" : "生成复盘"
            }}</t-button
          >
        </div>
        <template v-if="selected.ai_review?.status === 'completed'">
          <p class="review-text">{{ selected.ai_review.report.summary }}</p>
          <h4>偏差分析</h4>
          <div
            v-for="(item, i) in selected.ai_review.report.findings"
            :key="i"
            class="ai-finding"
          >
            <p class="review-text">{{ item.text }}</p>
            <span class="small muted"
              >依据：{{ item.evidence_ids.map(evidenceTitle).join("、") }}</span
            >
          </div>
          <template v-if="selected.ai_review.report.hypotheses.length"
            ><h4>待验证解释</h4>
            <p
              v-for="(item, i) in selected.ai_review.report.hypotheses"
              :key="i"
              class="review-text"
            >
              {{ item.text }}
            </p></template
          >
          <template v-if="selected.ai_review.report.missing_data.length"
            ><h4>缺失资料</h4>
            <p class="review-text">
              {{ selected.ai_review.report.missing_data.join("；") }}
            </p></template
          >
          <template v-if="selected.ai_review.report.next_checks.length"
            ><h4>后续检查</h4>
            <ul>
              <li
                v-for="(item, i) in selected.ai_review.report.next_checks"
                :key="i"
              >
                {{ item }}
              </li>
            </ul></template
          >
          <p class="small muted">
            {{ selected.ai_review.model }} ·
            {{ timeLabel(selected.ai_review.updated_at) }}
          </p>
          <details>
            <summary>查看依据</summary>
            <div
              v-for="item in selected.ai_review.evidence"
              :key="item.id"
              class="ai-evidence"
            >
              <strong>{{ evidenceTitle(item.id) }}</strong>
              <p class="review-text">{{ evidenceText(item) }}</p>
            </div>
          </details>
        </template>
        <p v-else class="muted">
          {{
            selected.ai_review?.error ||
            aiLabels[selected.ai_review?.status] ||
            "待生成"
          }}
        </p>
      </section>
      <div class="note-editor flush mt16">
        <h3>我的复盘</h3>
        <t-textarea
          v-model="draft"
          placeholder="记录判断偏差、关键事件与后续改进…"
          :maxlength="2000"
          :rows="5"
          aria-label="复盘笔记"
        />
        <p class="muted small">赛后笔记独立保存，不改变赛前预测。</p>
      </div></template
    ></t-drawer
  >
</template>

<style scoped>
.ai-review .panel-heading {
  padding: 0;
}
.ai-review h4 {
  margin: 20px 0 8px;
  font-size: 14px;
}
.ai-finding {
  margin-bottom: 16px;
}
.ai-finding p {
  margin-bottom: 4px;
}
.ai-review li {
  margin: 8px 0;
  line-height: 1.7;
}
.ai-evidence {
  margin-top: 16px;
}
.ai-evidence pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
}
</style>

<script setup>
import { ref, computed, watch } from "vue";
import { MessagePlugin } from "tdesign-vue-next";
import { RefreshIcon, SearchIcon } from "tdesign-icons-vue-next";
import { demoReviews } from "./demo";
import { request, toCsv } from "./domain";
const props = defineProps({ mode: String, notes: Object, versions: Object });
const emit = defineEmits(["save-note", "download"]);
const rows = ref([]),
  loading = ref(false),
  error = ref(""),
  query = ref(""),
  status = ref(""),
  selected = ref(null),
  draft = ref(""),
  open = ref(false),
  page = ref(1);
const list = computed(() =>
  rows.value
    .map((r) => ({ ...r, status: props.notes[r.id] ? "已复盘" : r.status }))
    .filter(
      (r) =>
        (!status.value || r.status === status.value) &&
        `${r.home}${r.away}`.includes(query.value),
    ),
);
const pageRows = computed(() =>
  list.value.slice((page.value - 1) * 10, page.value * 10),
);
watch([query, status], () => (page.value = 1));
async function load() {
  loading.value = true;
  error.value = "";
  try {
    if (props.mode === "demo") rows.value = structuredClone(demoReviews);
    else {
      const d = await request("/api/analyze/reviews");
      rows.value = Object.entries(d.reviews || {}).map(([id, r]) => ({
        id,
        date: r.generated_at?.slice(0, 10) || "—",
        league: "—",
        home: r.home,
        away: r.away,
        pick: r.direction || "—",
        score: r.actual_score || "—",
        consistent: r.had_won,
        status: r.reply ? "已复盘" : "待复盘",
        reply: r.reply,
        model: r.model,
      }));
    }
  } catch (e) {
    error.value = e.message;
    rows.value = [];
  } finally {
    loading.value = false;
  }
}
watch(() => props.mode, load, { immediate: true });
function inspect(row) {
  selected.value = row;
  draft.value = props.notes[row.id] || "";
  open.value = true;
}
function save() {
  emit("save-note", { id: selected.value.id, note: draft.value });
  MessagePlugin.success("复盘笔记已保存");
  open.value = false;
}
function exportCsv() {
  emit(
    "download",
    toCsv(
      list.value.map((r) => ({
        ...r,
        fixture: `${r.home} vs ${r.away}`,
        note: props.notes[r.id] || "",
      })),
      [
        { title: "日期", key: "date" },
        { title: "比赛", key: "fixture" },
        { title: "赛前方向", key: "pick" },
        { title: "实际比分", key: "score" },
        { title: "复盘状态", key: "status" },
        { title: "复盘笔记", key: "note" },
      ],
    ),
    `${props.mode === "demo" ? "示例-" : ""}复盘记录.csv`,
  );
}
const columns = [
  { colKey: "date", title: "日期", width: 120 },
  { colKey: "league", title: "赛事", width: 90 },
  { colKey: "fixture", title: "比赛", minWidth: 230 },
  { colKey: "pick", title: "赛前方向", width: 110 },
  { colKey: "score", title: "实际比分", width: 110 },
  { colKey: "result", title: "方向对照", width: 130 },
  { colKey: "status", title: "复盘状态", width: 110 },
  { colKey: "action", title: "操作", width: 90 },
];
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">REVIEW</div>
      <h1>复盘记录</h1>
    </div>
    <div class="actions">
      <t-button variant="outline" :loading="loading" @click="load"
        ><template #icon><RefreshIcon /></template>刷新</t-button
      ><t-button variant="outline" :disabled="!list.length" @click="exportCsv"
        >导出记录</t-button
      >
    </div>
  </div>
  <div class="summary-strip">
    <div>
      <span>记录场次</span><strong>{{ rows.length }}</strong>
    </div>
    <div>
      <span>已复盘</span
      ><strong>{{
        rows.filter((r) => r.status === "已复盘" || notes[r.id]).length
      }}</strong>
    </div>
    <div>
      <span>待复盘</span
      ><strong>{{
        rows.filter((r) => r.status !== "已复盘" && !notes[r.id]).length
      }}</strong>
    </div>
    <div>
      <span>归档原则</span>
      <p>
        保留赛前判断<br /><span class="muted small"
          >复盘补充不改写原始记录</span
        >
      </p>
    </div>
  </div>
  <section class="panel">
    <div class="panel-heading">
      <div class="actions">
        <h2>赛后对照</h2>
        <t-select
          v-model="status"
          clearable
          placeholder="全部状态"
          aria-label="复盘状态"
          style="width: 130px"
          :options="[
            { label: '待复盘', value: '待复盘' },
            { label: '已复盘', value: '已复盘' },
          ]"
        />
      </div>
      <t-input
        v-model="query"
        clearable
        placeholder="搜索球队"
        aria-label="搜索复盘球队"
        style="width: 220px"
        ><template #prefix-icon><SearchIcon /></template
      ></t-input>
    </div>
    <div v-if="error" class="empty-state">
      <h3>复盘记录加载失败</h3>
      <p>{{ error }}</p>
      <t-button @click="load">重试</t-button>
    </div>
    <t-table
      v-else
      row-key="id"
      :data="pageRows"
      :columns="columns"
      :loading="loading"
      hover
      empty="暂无符合条件的复盘记录。"
      ><template #fixture="{ row }"
        ><strong>{{ row.home }}</strong
        ><span class="versus">vs</span><strong>{{ row.away }}</strong></template
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
                : "待核实"
          }}</span
        ></template
      ><template #status="{ row }"
        ><t-tag
          :theme="row.status === '待复盘' ? 'warning' : 'default'"
          variant="light"
          >{{ row.status }}</t-tag
        ></template
      ><template #action="{ row }"
        ><t-button theme="primary" variant="text" @click="inspect(row)"
          >查看复盘</t-button
        ></template
      ></t-table
    >
    <div class="table-footer">
      <span class="small muted">{{
        mode === "demo" ? "示例比赛与赛果" : "来源：现有 AI 复盘缓存"
      }}</span
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
  <div class="page-footnote">
    单场方向一致不代表模型整体有效。模型评估请查看“数据与模型”。
  </div>
  <t-drawer
    v-model:visible="open"
    header="比赛复盘"
    size="640px"
    :close-on-esc-keydown="true"
    :close-on-overlay-click="false"
    :confirm-btn="{
      content: '保存笔记',
      disabled: draft === (notes[selected?.id] || ''),
    }"
    cancel-btn="关闭"
    @confirm="save"
    @close="open = false"
    @cancel="open = false"
  >
    <template v-if="selected"
      ><h2>
        {{ selected.home }} <span class="versus">vs</span> {{ selected.away }}
      </h2>
      <p class="muted">
        {{ selected.date }} ·
        {{ mode === "demo" ? "示例赛后记录" : "历史复盘记录" }}
      </p>
      <div class="review-comparison">
        <div>
          <span class="label-caption">赛前方向 · 只读</span
          ><strong>{{ selected.pick }}</strong>
        </div>
        <div>
          <span class="label-caption">实际比分</span
          ><strong>{{ selected.score }}</strong>
        </div>
      </div>
      <h3>赛后观察</h3>
      <p class="review-text">
        {{
          selected.reply ||
          (mode === "demo"
            ? selected.consistent
              ? "赛果与主胜方向一致，仍需区分判断质量与单场结果。回看首发、机会质量和关键事件。"
              : "比赛未按主胜方向发展。复核对客队防守强度的判断，并检查阵容变化是否及时进入分析。"
            : "暂无已生成的复盘正文。")
        }}
      </p>
      <div class="note-editor flush">
        <h3>复盘笔记</h3>
        <t-textarea
          v-model="draft"
          placeholder="记录偏差原因、可复用的观察和下次改进…"
          :maxlength="2000"
          :autosize="{ minRows: 6, maxRows: 12 }"
          aria-label="复盘笔记"
        />
        <p class="muted small">保存于当前浏览器，不修改原始预测。</p>
      </div></template
    >
  </t-drawer>
</template>

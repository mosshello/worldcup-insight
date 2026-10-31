<script setup>
import { ref, computed, onMounted } from "vue";
import { MessagePlugin } from "tdesign-vue-next";
import { RefreshIcon, SearchIcon } from "tdesign-icons-vue-next";
import { api, timeLabel } from "./api";
import { toCsv } from "./domain";
const props = defineProps({ state: Object });
const emit = defineEmits(["saved", "download"]);
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
      status: props.state.reviewNotes[r.id] ? "已复盘" : r.status,
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
  { colKey: "score", title: "实际比分", width: 110 },
  { colKey: "result", title: "方向对照", width: 110 },
  { colKey: "status", title: "复盘状态", width: 100 },
  { colKey: "action", title: "操作", width: 100 },
];
onMounted(() => load(true));
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">REVIEW</div>
      <h1>复盘记录</h1>
    </div>
    <div class="actions">
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
      ><strong>{{ rows.filter((r) => r.consistent !== null).length }}</strong>
    </div>
    <div>
      <span>已写复盘</span
      ><strong>{{ rows.filter((r) => state.reviewNotes[r.id]).length }}</strong>
    </div>
    <div>
      <span>对照规则</span>
      <p>
        使用最后一版赛前预测<br /><span class="muted small"
          >保留全部版本与生成时的数据</span
        >
      </p>
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
          variant="light"
          :theme="row.status === '待复盘' ? 'warning' : 'default'"
          >{{ row.status }}</t-tag
        ></template
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
    size="680px"
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

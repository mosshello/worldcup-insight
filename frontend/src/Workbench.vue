<script setup>
import {
  ref,
  reactive,
  computed,
  watch,
  onMounted,
  onBeforeUnmount,
} from "vue";
import { MessagePlugin } from "tdesign-vue-next";
import {
  SearchIcon,
  RefreshIcon,
  StarIcon,
  StarFilledIcon,
  ChevronRightIcon,
  InfoCircleIcon,
} from "tdesign-icons-vue-next";
import { api, adaptMatch, timeLabel, lifecycleNames, saleNames } from "./api";
import { marketNames, price, toCsv } from "./domain";
import MatchWorkspace from "./MatchWorkspace.vue";
import ReviewWorkspace from "./ReviewWorkspace.vue";
import ModelWorkspace from "./ModelWorkspace.vue";
import LiveScore from "./LiveScore.vue";
const hash = ref(location.hash.slice(1) || "/matches");
const route = computed(() => hash.value.split("/")[1] || "matches");
const matches = ref([]),
  loading = ref(false),
  error = ref(""),
  warning = ref(""),
  updated = ref(""),
  stateError = ref("");
const state = ref({
  favorites: [],
  notes: {},
  versions: {},
  reviewNotes: {},
  drafts: {},
});
const capabilities = ref({ ai_configured: false });
const reviewNotice = ref(null);
const reviewNoticeError = ref("");
let reviewCursor = null,
  reviewPolling = false,
  reviewTimer;
async function pollReviewUpdates() {
  if (reviewPolling || document.hidden) return;
  reviewPolling = true;
  try {
    const data = await api.reviewUpdates(reviewCursor);
    reviewCursor = data.cursor;
    reviewNoticeError.value = "";
    if (data.updates.length) {
      reviewNotice.value = data.updates[data.updates.length - 1];
    }
  } catch {
    reviewNoticeError.value = "分析更新连接暂不可用，正在重试";
  } finally {
    reviewPolling = false;
  }
}
const filter = reactive({
  date: "",
  league: [],
  prediction: "",
  market: "",
  query: "",
  single: false,
  followed: false,
  sale: "selling",
  phase: "",
});
const currentPage = ref(1),
  pageSize = ref(10);
const filtered = computed(() =>
  matches.value.filter(
    (m) =>
      (!filter.date ||
        (route.value === "scores" ? m.kickoff.slice(0, 10) : m.date) ===
          filter.date) &&
      (!filter.league.length || filter.league.includes(m.league)) &&
      (!filter.prediction ||
        (!stateError.value &&
          (filter.prediction === "predicted") ===
            Boolean(state.value.versions[m.id]?.length))) &&
      (!filter.market || m.markets.includes(filter.market)) &&
      (!filter.query ||
        `${m.home}${m.away}${m.num}`
          .toLowerCase()
          .includes(filter.query.trim().toLowerCase())) &&
      (!filter.single ||
        (filter.market ? m.pools[filter.market]?.single : m.single)) &&
      (!filter.followed || state.value.favorites.includes(m.id)) &&
      (!filter.sale || m.sale === filter.sale) &&
      (!filter.phase || m.lifecycle === filter.phase),
  ),
);
const pageRows = computed(() =>
  filtered.value.slice(
    (currentPage.value - 1) * pageSize.value,
    currentPage.value * pageSize.value,
  ),
);
watch(filter, () => (currentPage.value = 1), { deep: true });
watch(
  () => filtered.value.length,
  () =>
    (currentPage.value = Math.min(
      currentPage.value,
      Math.max(1, Math.ceil(filtered.value.length / pageSize.value)),
    )),
);
const leagues = computed(() =>
  [...new Set(matches.value.map((m) => m.league))].map((v) => ({
    label: v,
    value: v,
  })),
);
watch(
  route,
  (value, previous) => {
    if (value === "scores" || previous === "scores") {
      Object.assign(filter, {
        date: "",
        league: [],
        prediction: "",
        market: "",
        query: "",
        single: false,
        followed: false,
        sale: value === "scores" ? "" : "selling",
        phase: "",
      });
    }
  },
  { immediate: true },
);
const active = computed(() =>
  matches.value.find(
    (m) => m.id === decodeURIComponent(hash.value.split("/")[2] || ""),
  ),
);
const activeTab = computed(() => hash.value.split("/")[3] || "basic");
function navigate(path) {
  location.hash = path;
}
function hashChange() {
  hash.value = location.hash.slice(1) || "/matches";
  window.scrollTo(0, 0);
}
async function loadState() {
  try {
    const d = await api.state();
    state.value = d.state;
    stateError.value = "";
  } catch (e) {
    stateError.value = "记录服务不可用：" + e.message;
  }
}
async function reload(force = false) {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const data = await (force ? api.sync() : api.matches());
    matches.value = data.matches.map(adaptMatch);
    warning.value = data.warning
      ? `数据更新失败，当前显示缓存：${data.warning}`
      : "";
    updated.value = data.server_time;
    await loadState();
  } catch (e) {
    error.value = e.message;
    warning.value = matches.value.length
      ? "当前展示上次获取的数据，不能确认仍在售。"
      : "";
    matches.value = matches.value.map((m) => ({
      ...m,
      stale: true,
      sale: m.sale === "selling" ? "unknown" : m.sale,
    }));
  } finally {
    loading.value = false;
  }
}
const favoriteBusy = ref(new Set());
async function favorite(id) {
  if (favoriteBusy.value.has(id)) return;
  favoriteBusy.value.add(id);
  try {
    await api.save("favorites", id, !state.value.favorites.includes(id));
    await loadState();
  } catch (e) {
    MessagePlugin.error(e.message);
  } finally {
    favoriteBusy.value.delete(id);
  }
}
function download(text, filename) {
  const u = URL.createObjectURL(
    new Blob([text], { type: "text/csv;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = u;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(u), 30000);
}
function exportList() {
  download(
    toCsv(
      filtered.value.map((m) => ({
        ...m,
        fixture: `${m.home} vs ${m.away}`,
        odds: m.odds.map(price).join(" / "),
      })),
      [
        { key: "num", title: "编号" },
        { key: "league", title: "赛事" },
        { key: "fixture", title: "比赛" },
        { key: "date", title: "销售日" },
        { key: "kickoff", title: "北京时间" },
        { key: "odds", title: "胜平负SP" },
        { key: "captured_at", title: "采集时间" },
      ],
    ),
    "比赛列表.csv",
  );
}
const allColumns = [
  { colKey: "follow", title: "关注", width: 62 },
  { colKey: "num", title: "场次", width: 100 },
  { colKey: "league", title: "赛事", width: 82 },
  { colKey: "fixture", title: "比赛", minWidth: 240 },
  { colKey: "liveScore", title: "比分 / 进程", width: 200 },
  { colKey: "kickoff", title: "北京时间", width: 140 },
  { colKey: "sp", title: "胜 / 平 / 负 SP", width: 250 },
  { colKey: "eligibility", title: "销售", width: 88 },
  { colKey: "analysisState", title: "分析", width: 88 },
  { colKey: "coverage", title: "资料核查", width: 100 },
  { colKey: "action", title: "操作", width: 86, fixed: "right" },
];
const columns = computed(() =>
  route.value === "scores"
    ? allColumns.filter(
        (c) => !["sp", "eligibility", "analysisState"].includes(c.colKey),
      )
    : allColumns,
);
let timer;
onMounted(async () => {
  window.addEventListener("hashchange", hashChange);
  reload();
  pollReviewUpdates();
  reviewTimer = setInterval(pollReviewUpdates, 5000);
  try {
    capabilities.value = await api.capabilities();
  } catch {}
  timer = setInterval(() => {
    if (!document.hidden) reload();
  }, 30000);
});
onBeforeUnmount(() => {
  window.removeEventListener("hashchange", hashChange);
  clearInterval(timer);
  clearInterval(reviewTimer);
});
</script>
<template>
  <header class="app-header">
    <a href="#/matches" class="brand"
      ><span class="brand-mark">F</span><span>足球分析</span></a
    >
    <nav aria-label="主导航">
      <a
        href="#/matches"
        :class="{ active: ['matches', 'match'].includes(route) }"
        >比赛中心</a
      ><a href="#/scores" :class="{ active: route === 'scores' }">实时比分</a
      ><a href="#/reviews" :class="{ active: route === 'reviews' }">复盘记录</a
      ><a href="#/models" :class="{ active: route === 'models' }">数据与模型</a>
    </nav>
  </header>
  <main class="main-shell">
    <div
      v-if="reviewNotice || reviewNoticeError"
      class="panel panel-heading mb16"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span v-if="reviewNoticeError">{{ reviewNoticeError }}</span>
      <template v-else>
        <span
          >{{ reviewNotice.fixture }} ·
          {{
            reviewNotice.status === "preliminary"
              ? "完场初评已生成"
              : reviewNotice.status === "conflict"
                ? "赛果冲突待核实"
                : "赛果已确认，分析已更新"
          }}</span
        >
        <div class="actions">
          <a href="#/reviews" class="score-page-link">查看复盘</a>
          <t-button variant="text" @click="reviewNotice = null">收起</t-button>
        </div>
      </template>
    </div>
    <t-alert
      v-if="stateError"
      theme="warning"
      :message="stateError"
      class="mb16"
    />
    <template v-if="['matches', 'scores'].includes(route)">
      <div class="page-heading">
        <div>
          <h1>{{ route === "scores" ? "实时比分" : "比赛中心" }}</h1>
        </div>
        <div class="actions">
          <span class="small muted">{{
            updated ? "获取于 " + timeLabel(updated) : "正在连接数据源"
          }}</span
          ><t-button variant="outline" :loading="loading" @click="reload(true)"
            ><template #icon><RefreshIcon /></template>刷新</t-button
          >
        </div>
      </div>
      <t-alert v-if="warning" theme="warning" :message="warning" class="mb16" />
      <section class="panel filters">
        <div class="filter-field">
          <label>{{ route === "scores" ? "比赛日期" : "销售日期" }}</label
          ><t-date-picker
            v-model="filter.date"
            clearable
            placeholder="全部已采集日期"
            format="YYYY-MM-DD"
            :aria-label="route === 'scores' ? '比赛日期' : '销售日期'"
          />
        </div>
        <div class="filter-field">
          <label>赛事</label
          ><t-select
            v-model="filter.league"
            multiple
            :min-collapsed-num="1"
            clearable
            placeholder="全部赛事"
            :options="leagues"
            aria-label="赛事"
          />
        </div>
        <div v-if="route !== 'scores'" class="filter-field">
          <label>玩法</label
          ><t-select
            v-model="filter.market"
            clearable
            placeholder="全部玩法"
            :options="
              Object.entries(marketNames).map(([value, label]) => ({
                value,
                label,
              }))
            "
            aria-label="玩法"
          />
        </div>
        <div class="filter-field search-field">
          <label>搜索比赛</label
          ><t-input
            v-model="filter.query"
            clearable
            placeholder="球队 / 场次编号"
            aria-label="搜索比赛"
            ><template #prefix-icon><SearchIcon /></template
          ></t-input>
        </div>
        <div class="filter-bottom">
          <div class="actions">
            <t-checkbox v-if="route !== 'scores'" v-model="filter.single"
              >仅单关</t-checkbox
            ><t-checkbox v-model="filter.followed">仅关注</t-checkbox
            ><t-checkbox
              v-if="route !== 'scores'"
              :checked="filter.sale === 'selling'"
              @change="(v) => (filter.sale = v ? 'selling' : '')"
              >仅在售</t-checkbox
            ><t-select
              v-model="filter.phase"
              clearable
              placeholder="全部比赛状态"
              style="width: 145px"
              :options="
                Object.entries(lifecycleNames).map(([value, label]) => ({
                  value,
                  label,
                }))
              "
              aria-label="比赛状态"
            />
            <label for="prediction-filter">是否预测</label>
            <t-select
              v-model="filter.prediction"
              :input-props="{ id: 'prediction-filter' }"
              style="width: 120px"
              :disabled="!!stateError"
              :options="[
                { label: '全部', value: '' },
                { label: '已预测', value: 'predicted' },
                { label: '未预测', value: 'unpredicted' },
              ]"
              aria-label="是否预测"
            />
          </div>
          <t-button
            variant="text"
            @click="
              Object.assign(filter, {
                date: '',
                league: [],
                prediction: '',
                market: '',
                query: '',
                single: false,
                followed: false,
                sale: route === 'scores' ? '' : 'selling',
                phase: '',
              })
            "
            >重置筛选</t-button
          >
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading">
          <div class="actions">
            <h2>比赛列表</h2>
            <span class="count">{{ filtered.length }}</span>
          </div>
          <t-button
            variant="outline"
            :disabled="!filtered.length"
            @click="exportList"
            >导出列表</t-button
          >
        </div>
        <div v-if="error && !matches.length" class="empty-state">
          <InfoCircleIcon />
          <h3>暂时无法获取比赛</h3>
          <p>{{ error }}</p>
          <t-button @click="reload(true)">重新获取</t-button>
        </div>
        <t-table
          v-else
          class="desktop-matches"
          row-key="id"
          :data="pageRows"
          :columns="columns"
          :loading="loading"
          hover
          table-layout="fixed"
          empty="没有符合条件的已采集比赛。可清除筛选，或等待官方公布场次。"
          ><template #follow="{ row }"
            ><button
              class="icon-button"
              :class="{ selected: state.favorites.includes(row.id) }"
              :disabled="favoriteBusy.has(row.id) || !!stateError"
              :aria-label="`${state.favorites.includes(row.id) ? '取消关注' : '关注'}${row.home}`"
              :aria-pressed="state.favorites.includes(row.id)"
              @click="favorite(row.id)"
            >
              <StarFilledIcon
                v-if="state.favorites.includes(row.id)"
              /><StarIcon v-else /></button></template
          ><template #fixture="{ row }"
            ><button
              class="fixture-link"
              @click="navigate(`/match/${row.id}/basic`)"
            >
              <strong>{{ row.home }}</strong
              ><span class="versus">vs</span><strong>{{ row.away }}</strong>
            </button></template
          ><template #kickoff="{ row }"
            ><div class="fixture-schedule">
              <span class="mono"
                >{{ row.kickoff.slice(5, 10) }} {{ row.time }}</span
              >
            </div></template
          ><template #liveScore="{ row }"
            ><LiveScore :value="row.live_score" compact /></template
          ><template #sp="{ row }"
            ><div class="market-price">
              <div class="odds-triplet">
                <span v-for="(v, i) in row.odds" :key="i">{{ price(v) }}</span>
              </div>
              <span
                v-if="row.pools.had?.single"
                class="market-single"
                title="胜平负支持单关"
                aria-label="胜平负支持单关"
                >单关</span
              >
            </div></template
          ><template #eligibility="{ row }"
            ><span :class="row.sale === 'selling' ? 'success' : 'muted'">{{
              saleNames[row.sale]
            }}</span></template
          ><template #analysisState="{ row }"
            ><span
              :class="state.versions[row.id]?.length ? 'primary' : 'muted'"
              >{{ state.versions[row.id]?.length ? "已归档" : row.data_coverage?.total ? "自动研判" : "待采集" }}</span
            ></template
          ><template #coverage="{ row }"><a :href="`#/match/${row.id}/analysis`">{{ row.data_coverage?.total ? `${row.data_coverage.available}/${row.data_coverage.total}项` : '待采集' }}</a></template
          ><template #action="{ row }"
            ><t-button
              theme="primary"
              variant="text"
              @click="navigate(`/match/${row.id}/basic`)"
              >查看<ChevronRightIcon /></t-button></template
        ></t-table>
        <div v-if="!error || matches.length" class="mobile-matches">
          <article v-for="row in pageRows" :key="row.id" class="mobile-match">
            <div class="mobile-match-meta">
              <span>{{ row.league }} · {{ row.num }}</span
              ><span class="fixture-schedule"
                >{{ row.kickoff.slice(5, 10) }} {{ row.time }}</span
              >
            </div>
            <div class="mobile-match-heading">
              <LiveScore :value="row.live_score" />
              <button
                class="fixture-link"
                @click="navigate(`/match/${row.id}/basic`)"
              >
                <strong>{{ row.home }}</strong
                ><span class="versus">vs</span
                ><strong>{{ row.away }}</strong></button
              ><button
                class="icon-button"
                :class="{ selected: state.favorites.includes(row.id) }"
                :aria-label="`关注${row.home}`"
                :disabled="favoriteBusy.has(row.id) || !!stateError"
                @click="favorite(row.id)"
              >
                <StarFilledIcon
                  v-if="state.favorites.includes(row.id)"
                /><StarIcon v-else />
              </button>
            </div>
            <div class="mobile-match-bottom">
              <div v-if="route !== 'scores'" class="market-price">
                <div class="odds-triplet">
                  <span v-for="(v, i) in row.odds" :key="i"
                    ><small>{{ ["胜", "平", "负"][i] }}</small
                    >{{ price(v) }}</span
                  >
                </div>
                <span
                  v-if="row.pools.had?.single"
                  class="market-single"
                  title="胜平负支持单关"
                  aria-label="胜平负支持单关"
                  >单关</span
                >
              </div>
              <t-button
                theme="primary"
                variant="text"
                @click="navigate(`/match/${row.id}/basic`)"
                >查看<ChevronRightIcon
              /></t-button>
            </div>
            <div v-if="route !== 'scores'" class="small muted mt8">
              {{ saleNames[row.sale] }}
            </div>
          </article>
          <div v-if="!pageRows.length" class="empty-state compact">
            {{ loading ? "正在读取比赛…" : "没有符合条件的已采集比赛。" }}
          </div>
        </div>
        <div class="table-footer pagination-only">
          <t-pagination
            v-model="currentPage"
            v-model:page-size="pageSize"
            :total="filtered.length"
            :page-size-options="[10, 20, 50]"
            :show-jumper="false"
            size="small"
          />
        </div>
      </section>
    </template>
    <template v-else-if="route === 'match'"
      ><MatchWorkspace
        v-if="active"
        :key="active.id"
        :match="active"
        :tab="activeTab"
        :state="state"
        :ai-configured="capabilities.ai_configured"
        @navigate="navigate"
        @favorite="favorite(active.id)"
        @saved="loadState"
      />
      <div v-else class="panel empty-state">
        <h2>{{ loading ? "正在读取比赛…" : "比赛未找到" }}</h2>
        <p>{{ error || "该场次尚未采集，或链接已失效。" }}</p>
        <t-button @click="navigate('/matches')">返回比赛中心</t-button>
      </div></template
    >
    <ReviewWorkspace
      v-else-if="route === 'reviews'"
      :state="state"
      @saved="loadState"
      @download="download"
    />
    <ModelWorkspace v-else-if="route === 'models'" />
    <div v-else class="panel empty-state">
      <h2>页面不存在</h2>
      <t-button @click="navigate('/matches')">返回比赛中心</t-button>
    </div>
  </main>
</template>

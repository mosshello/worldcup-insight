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
import { demoMatches, demoDate } from "./demo";
import {
  filterMatches,
  normalizeLiveMatch,
  marketNames,
  price,
  request,
  toCsv,
} from "./domain";
import MatchDetail from "./MatchDetail.vue";
import Reviews from "./Reviews.vue";
import Models from "./Models.vue";
const mode = ref(localStorage.getItem("football:mode") || "demo");
const hash = ref(location.hash.slice(1) || "/matches");
const matches = ref([]),
  loading = ref(false),
  error = ref(""),
  updated = ref(""),
  cached = ref(false),
  sourceOpen = ref(false);
const store = ref(readStore());
function readStore() {
  try {
    return (
      JSON.parse(localStorage.getItem(`football:v1:${mode.value}`)) || {
        favorites: [],
        notes: {},
        versions: {},
        reviewNotes: {},
        plans: [],
      }
    );
  } catch {
    return {
      favorites: [],
      notes: {},
      versions: {},
      reviewNotes: {},
      plans: [],
    };
  }
}
watch(
  store,
  (s) => {
    try {
      localStorage.setItem(`football:v1:${mode.value}`, JSON.stringify(s));
    } catch {
      MessagePlugin.error("本地存储空间不足，当前修改未保存。");
    }
  },
  { deep: true },
);
const filter = reactive({
  date: mode.value === "demo" ? demoDate : "",
  league: "",
  market: "",
  query: "",
  single: false,
  followed: false,
  sale: "selling",
});
const currentPage = ref(1),
  pageSize = ref(10);
const filtered = computed(() =>
  filterMatches(matches.value, filter, store.value.favorites),
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
  () => {
    currentPage.value = Math.min(
      currentPage.value,
      Math.max(1, Math.ceil(filtered.value.length / pageSize.value)),
    );
  },
);
const leagues = computed(() =>
  [...new Set(matches.value.map((m) => m.league))].map((v) => ({
    label: v,
    value: v,
  })),
);
const dates = computed(() =>
  [...new Set(matches.value.map((m) => m.date).filter(Boolean))]
    .sort()
    .map((v) => ({ label: v, value: v })),
);
const route = computed(() => hash.value.split("/")[1] || "matches");
const active = computed(() =>
  matches.value.find((m) => m.id === hash.value.split("/")[2]),
);
const activeTab = computed(() => hash.value.split("/")[3] || "basic");
const title = computed(
  () =>
    ({
      matches: "比赛中心",
      match: "比赛中心",
      reviews: "复盘记录",
      models: "数据与模型",
    })[route.value] || "比赛中心",
);
function navigate(path) {
  location.hash = path;
}
function hashChange() {
  hash.value = location.hash.slice(1) || "/matches";
  window.scrollTo(0, 0);
}
let controller;
async function reload() {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  const thisMode = mode.value,
    signal = controller.signal;
  try {
    if (thisMode === "demo") {
      matches.value = structuredClone(demoMatches);
      updated.value = "示例快照 · 10-28 18:32";
      cached.value = false;
      return;
    }
    const data = await request("/api/sporttery/matches", { signal });
    if (signal.aborted) return;
    matches.value = (data.matches || []).map(normalizeLiveMatch);
    cached.value = !!data.cached;
    updated.value = data.cached
      ? `缓存快照 · ${data.cached_at || "时间未知"}`
      : `获取时间 · ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  } catch (e) {
    if (!signal.aborted) {
      error.value = e.message;
      matches.value = [];
      updated.value = "";
    }
  } finally {
    if (!signal.aborted) loading.value = false;
  }
}
async function changeMode(value) {
  mode.value = value;
  localStorage.setItem("football:mode", value);
  store.value = readStore();
  Object.assign(filter, {
    date: value === "demo" ? demoDate : "",
    league: "",
    market: "",
    query: "",
    single: false,
    followed: false,
    sale: "selling",
  });
  navigate("/matches");
  await reload();
}
function favorite(id) {
  const f = store.value.favorites;
  const idx = f.indexOf(id);
  if (idx < 0) f.push(id);
  else f.splice(idx, 1);
}
function saveVersion({ matchId, version }) {
  (store.value.versions[matchId] ??= []).unshift(version);
}
function download(text, filename, type = "text/csv;charset=utf-8") {
  const u = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = u;
  a.download = filename;
  document.body.appendChild(a);
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
      ],
    ),
    `${mode.value === "demo" ? "示例-" : ""}比赛列表.csv`,
  );
}
const columns = [
  { colKey: "follow", title: "关注", width: 62 },
  { colKey: "num", title: "场次", width: 100 },
  { colKey: "league", title: "赛事", width: 82 },
  { colKey: "fixture", title: "比赛", minWidth: 240 },
  { colKey: "kickoff", title: "北京时间", width: 140 },
  { colKey: "sp", title: "胜 / 平 / 负 SP", width: 236 },
  { colKey: "eligibility", title: "开售情况", width: 130 },
  { colKey: "analysisState", title: "分析", width: 100 },
  { colKey: "action", title: "操作", width: 92, fixed: "right" },
];
onMounted(() => {
  window.addEventListener("hashchange", hashChange);
  reload();
});
onBeforeUnmount(() => {
  window.removeEventListener("hashchange", hashChange);
  controller?.abort();
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
      ><a href="#/reviews" :class="{ active: route === 'reviews' }">复盘记录</a
      ><a href="#/models" :class="{ active: route === 'models' }">数据与模型</a>
    </nav>
    <div class="header-right">
      <t-select
        :value="mode"
        aria-label="数据模式"
        class="mode-select"
        :options="[
          { label: '示例数据', value: 'demo' },
          { label: '真实接口', value: 'live' },
        ]"
        @change="changeMode"
      /><span class="workspace-label">个人工作台</span>
    </div>
  </header>
  <div class="context-bar">
    <span
      ><span class="status-dot" :class="{ demo: mode === 'demo' }"></span
      >{{
        mode === "demo"
          ? "示例数据 · 非真实赛程与预测"
          : "真实接口 · 体彩公布场次"
      }}<span class="context-extra">{{
        mode === "demo"
          ? "用于页面与交互确认"
          : "场次及开售状态以官方最新发布为准"
      }}</span></span
    ><button class="text-action" @click="sourceOpen = true">
      <InfoCircleIcon />数据说明
    </button>
  </div>
  <main class="main-shell">
    <template v-if="route === 'matches'">
      <div class="page-heading">
        <div>
          <div class="eyebrow">MATCH CENTER</div>
          <h1>比赛中心</h1>
        </div>
        <div class="actions">
          <span class="muted small">{{ updated }}</span
          ><t-button variant="outline" :loading="loading" @click="reload"
            ><template #icon><RefreshIcon /></template>刷新</t-button
          >
        </div>
      </div>
      <t-alert
        v-if="cached"
        theme="warning"
        class="mb16"
        message="当前为历史缓存，不能据此确认仍在售。请刷新官方数据后核对。"
      />
      <section class="panel filters">
        <div class="filter-field">
          <label>销售日期</label
          ><t-select
            v-model="filter.date"
            clearable
            placeholder="全部日期"
            :options="dates"
            aria-label="销售日期"
          />
        </div>
        <div class="filter-field">
          <label>赛事</label
          ><t-select
            v-model="filter.league"
            clearable
            placeholder="全部赛事"
            :options="leagues"
            aria-label="赛事"
          />
        </div>
        <div class="filter-field">
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
            <t-checkbox v-model="filter.single">仅单关</t-checkbox
            ><t-checkbox v-model="filter.followed">仅关注</t-checkbox
            ><t-checkbox
              :checked="filter.sale === 'selling'"
              @change="(v) => (filter.sale = v ? 'selling' : '')"
              >仅在售</t-checkbox
            >
          </div>
          <t-button
            variant="text"
            @click="
              Object.assign(filter, {
                date: '',
                league: '',
                market: '',
                query: '',
                single: false,
                followed: false,
                sale: 'selling',
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
        <div v-if="error" class="empty-state">
          <InfoCircleIcon />
          <h3>暂时无法获取比赛</h3>
          <p>{{ error }}</p>
          <t-button @click="reload">重新获取</t-button>
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
          :empty="
            matches.length
              ? '没有符合筛选条件的比赛，请调整筛选。'
              : '当前没有官方公布的场次。'
          "
        >
          <template #follow="{ row }"
            ><button
              class="icon-button"
              :class="{ selected: store.favorites.includes(row.id) }"
              :aria-label="`${store.favorites.includes(row.id) ? '取消关注' : '关注'}${row.home}`"
              :aria-pressed="store.favorites.includes(row.id)"
              @click="favorite(row.id)"
            >
              <StarFilledIcon
                v-if="store.favorites.includes(row.id)"
              /><StarIcon v-else /></button
          ></template>
          <template #fixture="{ row }"
            ><button
              class="fixture-link"
              @click="navigate(`/match/${row.id}/basic`)"
            >
              <strong>{{ row.home }}</strong
              ><span class="versus">vs</span><strong>{{ row.away }}</strong>
            </button></template
          >
          <template #kickoff="{ row }"
            ><span class="mono"
              >{{ row.kickoff?.slice(5, 10) }} {{ row.time }}</span
            ></template
          >
          <template #sp="{ row }"
            ><div class="odds-triplet">
              <span v-for="(v, i) in row.odds" :key="i">{{ price(v) }}</span
              ><span v-if="!row.odds.length">待开售</span>
            </div></template
          >
          <template #eligibility="{ row }"
            ><span :class="row.sale === 'selling' ? 'success' : 'muted'">{{
              row.sale === "selling" ? "已开售" : "待开售"
            }}</span
            ><span v-if="row.single" class="single-tag">单关</span></template
          >
          <template #analysisState="{ row }"
            ><span
              :class="
                store.versions[row.id]?.length || row.analysisState === '已分析'
                  ? 'primary'
                  : 'muted'
              "
              >{{
                store.versions[row.id]?.length ? "已分析" : row.analysisState
              }}</span
            ></template
          >
          <template #action="{ row }"
            ><t-button
              variant="text"
              theme="primary"
              @click="navigate(`/match/${row.id}/basic`)"
              >查看<ChevronRightIcon /></t-button
          ></template>
        </t-table>
        <div v-if="!error" class="mobile-matches">
          <article v-for="row in pageRows" :key="row.id" class="mobile-match">
            <div class="mobile-match-meta">
              <span>{{ row.league }} · {{ row.num }}</span
              ><span>{{ row.kickoff?.slice(5, 10) }} {{ row.time }}</span>
            </div>
            <div class="mobile-match-heading">
              <button
                class="fixture-link"
                @click="navigate(`/match/${row.id}/basic`)"
              >
                <strong>{{ row.home }}</strong
                ><span class="versus">vs</span
                ><strong>{{ row.away }}</strong></button
              ><button
                class="icon-button"
                :class="{ selected: store.favorites.includes(row.id) }"
                :aria-label="`${store.favorites.includes(row.id) ? '取消关注' : '关注'}${row.home}`"
                :aria-pressed="store.favorites.includes(row.id)"
                @click="favorite(row.id)"
              >
                <StarFilledIcon
                  v-if="store.favorites.includes(row.id)"
                /><StarIcon v-else />
              </button>
            </div>
            <div class="mobile-match-bottom">
              <div class="odds-triplet">
                <span v-for="(v, i) in row.odds" :key="i"
                  ><small>{{ ["胜", "平", "负"][i] }}</small
                  >{{ price(v) }}</span
                ><span v-if="!row.odds.length">待开售</span>
              </div>
              <t-button
                theme="primary"
                variant="text"
                @click="navigate(`/match/${row.id}/basic`)"
                >查看<ChevronRightIcon
              /></t-button>
            </div>
          </article>
          <div v-if="!pageRows.length" class="empty-state compact">
            {{ loading ? "正在读取比赛…" : "没有符合筛选条件的比赛。" }}
          </div>
        </div>
        <div class="table-footer">
          <span class="muted small">销售日与开赛日可能不同 · 北京时间</span
          ><t-pagination
            v-model="currentPage"
            v-model:page-size="pageSize"
            :total="filtered.length"
            :page-size-options="[10, 20, 50]"
            :show-jumper="false"
            size="small"
          />
        </div>
      </section>
      <div class="page-footnote">
        比赛范围：官方竞彩足球场次　<span>数据缺失以“—”标记</span>
      </div>
    </template>
    <template v-else-if="route === 'match'">
      <MatchDetail
        v-if="active"
        :key="`${mode}-${active.id}`"
        :match="active"
        :tab="activeTab"
        :mode="mode"
        :versions="store.versions[active.id] || []"
        :note="store.notes[active.id] || ''"
        :followed="store.favorites.includes(active.id)"
        :loading="loading"
        @navigate="navigate"
        @favorite="favorite(active.id)"
        @refresh="reload"
        @save-version="saveVersion"
        @save-note="(v) => (store.notes[active.id] = v)"
      />
      <div v-else class="panel empty-state">
        <h2>{{ loading ? "正在读取比赛…" : "比赛未在当前数据中找到" }}</h2>
        <p>{{ error || "该比赛可能已经移出官方列表，或属于另一数据模式。" }}</p>
        <t-button @click="navigate('/matches')">返回比赛中心</t-button>
      </div>
    </template>
    <Reviews
      v-else-if="route === 'reviews'"
      :mode="mode"
      :notes="store.reviewNotes"
      :versions="store.versions"
      @save-note="({ id, note }) => (store.reviewNotes[id] = note)"
      @download="download"
    />
    <Models
      v-else-if="route === 'models'"
      :mode="mode"
      :plans="store.plans"
      @save-plan="(p) => store.plans.unshift(p)"
      @delete-plan="
        (id) => (store.plans = store.plans.filter((p) => p.id !== id))
      "
    />
    <div v-else class="panel empty-state">
      <h2>页面不存在</h2>
      <t-button @click="navigate('/matches')">返回比赛中心</t-button>
    </div>
  </main>
  <t-drawer
    v-model:visible="sourceOpen"
    header="数据说明"
    :footer="false"
    :close-on-esc-keydown="true"
    size="480px"
    @close="sourceOpen = false"
  >
    <div class="drawer-body">
      <h3>当前模式：{{ mode === "demo" ? "示例数据" : "真实接口" }}</h3>
      <p>
        示例模式用于确认页面和交互，球队、赛程、SP、阵容、模型结果均为虚构示例，不对应实际比赛。
      </p>
      <h3>场次范围</h3>
      <p>
        比赛中心只读取体彩场次列表。欧冠、英超等为赛事分类，是否入选与在售以官方为准。销售日期、北京时间与场次编号分别保留。
      </p>
      <h3>赔率与分析</h3>
      <p>
        体彩 SP
        是固定奖金数据。外盘单独展示。市场定价与模型预测分别注明来源，缺失数据不使用示例补齐。
      </p>
      <h3>保存范围</h3>
      <p>
        关注、笔记、分析快照和训练配置保存在当前浏览器。示例与真实接口独立保存，尚未提供账号同步。
      </p>
      <h3>接口覆盖</h3>
      <p>
        已接入现有场次、融合分析及复盘接口。俱乐部阵容、xG
        和训练执行接口尚待接入。
      </p>
    </div>
  </t-drawer>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from "vue";
import { MessagePlugin } from "tdesign-vue-next";
import {
  ArrowLeftIcon,
  RefreshIcon,
  StarIcon,
  StarFilledIcon,
  ChevronRightIcon,
  CopyIcon,
} from "tdesign-icons-vue-next";
import { price, marketNames, request } from "./domain";
import { demoAnalysis } from "./demo";
const props = defineProps({
  match: Object,
  tab: String,
  mode: String,
  versions: Array,
  note: String,
  followed: Boolean,
  loading: Boolean,
});
const emit = defineEmits([
  "navigate",
  "favorite",
  "refresh",
  "save-version",
  "save-note",
]);
const scope = ref("all"),
  market = ref("had"),
  oddsSource = ref("sporttery"),
  chartRange = ref("24"),
  analysisBusy = ref(false),
  analysisError = ref(""),
  versionId = ref(""),
  noteDraft = ref(props.note),
  copyOpen = ref(false),
  copyText = ref(""),
  autoRefresh = ref(false),
  historical = ref(false);
const tabs = [
  ["basic", "基本面"],
  ["lineups", "阵容情报"],
  ["odds", "赔率"],
  ["analysis", "AI 分析"],
];
const initial = props.match.isDemo ? demoAnalysis(props.match) : null;
const versions = computed(() =>
  props.versions.length ? props.versions : initial ? [initial] : [],
);
const analysis = computed(
  () =>
    versions.value.find((a) => a.id === versionId.value) || versions.value[0],
);
const form = computed(
  () =>
    props.match.form?.map((rows, i) =>
      rows.filter(
        (r) => scope.value === "all" || r[3] === (i === 0 ? "主" : "客"),
      ),
    ) || [],
);
const basicStats = computed(() => {
  if (!form.value.length) return [];
  const stat = form.value.map((rows) => {
    let w = 0,
      d = 0,
      l = 0,
      g = 0,
      a = 0;
    rows.forEach((r) => {
      const [x, y] = r[4].split("-").map(Number);
      g += x;
      a += y;
      if (x > y) w++;
      else if (x === y) d++;
      else l++;
    });
    return {
      record: `${w}胜${d}平${l}负`,
      goals: (g / rows.length).toFixed(2),
      against: (a / rows.length).toFixed(2),
      n: rows.length,
    };
  });
  return [
    ["统计样本", ...stat.map((s) => `${s.n} 场`)],
    ["近期战绩", ...stat.map((s) => s.record)],
    ["场均进球", ...stat.map((s) => s.goals)],
    ["场均失球", ...stat.map((s) => s.against)],
    ...(scope.value === "all" ? props.match.stats.slice(3) : []),
  ];
});
const formCols = [
  { colKey: "date", title: "日期", width: 72 },
  { colKey: "league", title: "赛事", width: 65 },
  { colKey: "opponent", title: "对手", minWidth: 110 },
  { colKey: "venue", title: "主客", width: 60 },
  { colKey: "score", title: "比分", width: 65 },
  { colKey: "result", title: "赛果", width: 65 },
];
function formRows(i) {
  return (form.value[i] || []).map((r, j) => ({
    id: j,
    date: r[0],
    league: r[1],
    opponent: r[2],
    venue: r[3],
    score: r[4],
    result:
      Number(r[4][0]) > Number(r[4][2])
        ? "胜"
        : r[4][0] === r[4][2]
          ? "平"
          : "负",
  }));
}
const oddsCols = [
  { colKey: "option", title: "选项", minWidth: 140 },
  { colKey: "initial", title: "初始 SP", width: 110 },
  { colKey: "current", title: "当前 SP", width: 110 },
  { colKey: "change", title: "变化", width: 110 },
  { colKey: "updated", title: "更新时间", width: 140 },
];
const oddsRows = computed(() => {
  if (!props.match.markets.includes(market.value)) return [];
  if (!props.match.isDemo) {
    const p = props.match.raw[market.value];
    return p
      ? ["主胜", "平局", "客胜"].map((v, i) => ({
          option: v,
          initial: null,
          current: [p.home, p.draw, p.away][i],
          updated: p.updated_at || "未知",
        }))
      : [];
  }
  const data = {
    had: [
      ["主胜", props.match.odds[0] + 0.1, props.match.odds[0]],
      ["平局", props.match.odds[1] - 0.05, props.match.odds[1]],
      ["客胜", props.match.odds[2] - 0.2, props.match.odds[2]],
    ],
    hhad: [
      ["让球胜（-1）", 3.85, 3.7],
      ["让球平（-1）", 3.5, 3.4],
      ["让球负（-1）", 1.7, 1.73],
    ],
    ttg: [
      ["0球", 12, 11.5],
      ["1球", 4.2, 4.3],
      ["2球", 3.2, 3.1],
      ["3球", 3.4, 3.45],
      ["4球", 6.5, 6.2],
      ["5球", 12, 12],
      ["6球", 20, 21],
      ["7+球", 32, 33],
    ],
    crs: [
      ["1-0", 7, 6.8],
      ["1-1", 6.5, 6.25],
      ["2-0", 9, 8.5],
      ["2-1", 7.5, 7.25],
      ["0-1", 10, 10.5],
      ["0-0", 12, 11.5],
    ],
    hafu: [
      ["胜胜", 3, 2.85],
      ["胜平", 15, 15],
      ["胜负", 30, 31],
      ["平胜", 5, 4.8],
      ["平平", 5.5, 5.4],
      ["平负", 7, 7.1],
      ["负胜", 22, 23],
      ["负平", 15, 16],
      ["负负", 5.6, 5.8],
    ],
  };
  return data[market.value].map(([option, initial, current]) => ({
    option,
    initial,
    current,
    change: current - initial,
    updated: "10-28 18:32",
  }));
});
const history = computed(() =>
  market.value === "had"
    ? props.match.history.filter(
        (r) => chartRange.value !== "6" || r.time >= "12:32",
      )
    : [],
);
function linePoints(key) {
  const vals = history.value.map((r) => r[key]);
  const all = history.value.flatMap((r) => [r.h, r.d, r.a]);
  const lo = Math.min(...all) - 0.2,
    hi = Math.max(...all) + 0.2;
  return vals
    .map(
      (v, i) =>
        `${40 + (i * 680) / Math.max(vals.length - 1, 1)},${180 - ((v - lo) / (hi - lo)) * 140}`,
    )
    .join(" ");
}
function formationRows(i) {
  const names = props.match.lineups.names[i];
  const sizes = i === 0 ? [1, 4, 3, 3] : [1, 3, 5, 2];
  let offset = 0;
  return sizes.map((n) => {
    const result = names.slice(offset, offset + n);
    offset += n;
    return result;
  });
}
async function generate() {
  analysisBusy.value = true;
  analysisError.value = "";
  try {
    let result;
    if (props.mode === "demo")
      result = demoAnalysis(
        props.match,
        Math.max(0, ...versions.value.map((v) => v.version)) + 1,
      );
    else {
      const data = await request(
        `/api/sporttery/predict/${encodeURIComponent(props.match.id)}`,
      );
      const p = data.fusion || data.prediction || data;
      const probs = p.probabilities || {};
      result = {
        id: crypto.randomUUID(),
        version: versions.value.length + 1,
        created: new Date().toISOString(),
        matchId: props.match.id,
        title: p.direction ? `市场方向：${p.direction}` : "接口分析",
        summary: "现有接口输出市场定价分析，尚不代表俱乐部训练模型。",
        probabilities: ["home", "draw", "away"].map(
          (k) => Number(probs[k] ?? 0) * 100,
        ),
        probLabel: "体彩市场隐含概率",
        goals: "—",
        scores: "—",
        evidence: (p.analysis || []).map((text) => ({
          title: "接口依据",
          text,
          tab: "odds",
        })),
        risks: ["俱乐部基本面与阵容数据尚未接入。"],
        isDemo: false,
      };
    }
    if (props.mode === "demo" && !props.versions.length && initial)
      emit("save-version", { matchId: props.match.id, version: initial });
    emit("save-version", { matchId: props.match.id, version: result });
    versionId.value = result.id;
    MessagePlugin.success(
      props.mode === "demo" ? "已保存新的示例版本" : "分析快照已保存",
    );
  } catch (e) {
    analysisError.value = e.message;
  } finally {
    analysisBusy.value = false;
  }
}
async function copyAnalysis() {
  const a = analysis.value;
  if (!a) return;
  const text = `${props.match.home} vs ${props.match.away}\n${a.isDemo ? "【示例分析】\n" : ""}${a.title}\n${a.summary}\n${a.evidence.map((e) => e.text).join("\n")}\n风险：${a.risks.join("；")}\n版本：v${a.version}`;
  try {
    await navigator.clipboard.writeText(text);
    MessagePlugin.success("已复制分析");
  } catch {
    copyText.value = text;
    copyOpen.value = true;
  }
}
function saveNote() {
  emit("save-note", noteDraft.value);
  MessagePlugin.success("笔记已保存到当前浏览器");
}
let timer;
watch(autoRefresh, (on) => {
  clearInterval(timer);
  if (on)
    timer = setInterval(() => {
      if (!document.hidden && !props.loading) emit("refresh");
    }, 60000);
});
onBeforeUnmount(() => clearInterval(timer));
const historyCols = [
  { colKey: "time", title: "时间" },
  { colKey: "h", title: "主胜" },
  { colKey: "d", title: "平局" },
  { colKey: "a", title: "客胜" },
];
</script>

<template>
  <div class="breadcrumb">
    <a href="#/matches"><ArrowLeftIcon />比赛中心</a><span>/</span
    ><span>{{ match.num }}</span>
  </div>
  <section class="panel match-header">
    <div>
      <div class="match-meta">
        <t-tag variant="light" theme="primary">{{ match.league }}</t-tag
        ><span
          >{{ match.kickoff?.slice(0, 10) }} {{ match.time }} · 北京时间</span
        ><span>{{ match.num }}</span>
      </div>
      <h1>{{ match.home }} <span class="versus">vs</span> {{ match.away }}</h1>
    </div>
    <div class="actions">
      <t-button
        variant="outline"
        :theme="followed ? 'primary' : 'default'"
        @click="emit('favorite')"
        ><template #icon
          ><StarFilledIcon v-if="followed" /><StarIcon v-else /></template
        >{{ followed ? "已关注" : "关注比赛" }}</t-button
      ><t-button variant="outline" :loading="loading" @click="emit('refresh')"
        ><template #icon><RefreshIcon /></template>刷新数据</t-button
      >
    </div>
  </section>
  <t-tabs
    :value="tab"
    class="detail-tabs"
    @change="(v) => emit('navigate', `/match/${match.id}/${v}`)"
    ><t-tab-panel
      v-for="[value, label] in tabs"
      :key="value"
      :value="value"
      :label="label"
  /></t-tabs>
  <template v-if="tab === 'basic'">
    <div class="two-column detail-grid">
      <section class="panel">
        <div class="panel-heading">
          <h2>球队对比</h2>
          <t-radio-group
            v-model="scope"
            variant="default-filled"
            size="small"
            :options="[
              { label: '近5场', value: 'all' },
              { label: '主 / 客场', value: 'venue' },
            ]"
          />
        </div>
        <template v-if="basicStats.length"
          ><div class="compare-header">
            <strong>{{ match.home }}</strong
            ><span>指标</span><strong>{{ match.away }}</strong>
          </div>
          <div
            class="compare-row"
            v-for="[label, a, b] in basicStats"
            :key="label"
          >
            <strong>{{ a }}</strong
            ><span>{{ label }}</span
            ><strong>{{ b }}</strong>
          </div>
          <div class="section-note">
            {{
              scope === "all"
                ? "最近5场，各项赛事"
                : "最近5场内，主队主场 / 客队客场"
            }}
            · 示例统计
          </div></template
        >
        <div v-else class="empty-state compact">
          <h3>基本面数据待接入</h3>
          <p>暂无可验证的近期赛果和 xG 数据。</p>
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading"><h2>比赛背景</h2></div>
        <dl class="context-list">
          <div>
            <dt>赛事</dt>
            <dd>{{ match.league }}</dd>
          </div>
          <div>
            <dt>比赛阶段</dt>
            <dd>{{ match.isDemo ? "联赛阶段 · 第3轮（示例）" : "—" }}</dd>
          </div>
          <div>
            <dt>场地</dt>
            <dd>{{ match.isDemo ? "主队主场（示例）" : "—" }}</dd>
          </div>
          <div>
            <dt>休息天数</dt>
            <dd>{{ match.isDemo ? "主队 4 天 / 客队 3 天（示例）" : "—" }}</dd>
          </div>
          <div>
            <dt>下一场赛程</dt>
            <dd>
              {{ match.isDemo ? "主队 3 天后 / 客队 4 天后（示例）" : "—" }}
            </dd>
          </div>
          <div>
            <dt>天气</dt>
            <dd class="muted">暂无已核实数据</dd>
          </div>
        </dl>
        <div class="context-callout">
          <span class="label-caption">重点观察</span>
          <p>
            {{
              match.isDemo
                ? "客队休息时间较短，需结合首发判断是否轮换。"
                : "补齐伤停、赛程和阵容后，再判断可能影响比赛的因素。"
            }}
          </p>
        </div>
      </section>
    </div>
    <div class="two-column detail-grid">
      <section
        class="panel"
        v-for="(team, i) in [match.home, match.away]"
        :key="team"
      >
        <div class="panel-heading">
          <h2>{{ team }} · 近期赛果</h2>
          <span class="small muted">本队比分在前</span>
        </div>
        <t-table
          row-key="id"
          :data="formRows(i)"
          :columns="formCols"
          size="small"
          empty="暂无已核实赛果"
          ><template #result="{ row }"
            ><span
              class="result-square"
              :class="{ win: row.result === '胜', loss: row.result === '负' }"
              >{{ row.result }}</span
            ></template
          ></t-table
        >
      </section>
    </div>
  </template>
  <template v-else-if="tab === 'lineups'">
    <div class="inline-notice">
      {{
        match.lineups
          ? "预计阵容 · 位置示意，非实际球员首发；正式首发公布后需要复核。"
          : "阵容数据尚未接入，未确认球员不视为可出场。"
      }}
    </div>
    <div v-if="match.lineups" class="two-column detail-grid">
      <section
        class="panel"
        v-for="(team, i) in [match.home, match.away]"
        :key="team"
      >
        <div class="panel-heading">
          <h2>{{ team }}</h2>
          <span class="mono muted"
            >{{ match.lineups.formations[i] }} · 预计</span
          >
        </div>
        <div class="pitch" :aria-label="`${team}阵型示意`">
          <div class="pitch-line"></div>
          <div class="pitch-row" v-for="(row, j) in formationRows(i)" :key="j">
            <div class="player" v-for="(name, k) in row" :key="k">
              <span class="shirt">{{ j === 0 ? "GK" : j * 3 + k + 1 }}</span
              ><span>{{ name }}</span>
            </div>
          </div>
        </div>
        <div class="panel-heading subheading"><h3>伤停与出场状态</h3></div>
        <t-table
          row-key="player"
          :data="match.lineups.injuries[i]"
          :columns="[
            { colKey: 'player', title: '球员' },
            { colKey: 'issue', title: '情况' },
            { colKey: 'status', title: '确认状态' },
          ]"
          size="small"
          ><template #status="{ row }"
            ><span :class="row.status === '待确认' ? 'warning' : 'muted'"
              >{{ row.status }} · 示例</span
            ></template
          ></t-table
        >
      </section>
    </div>
    <div v-else class="panel empty-state">
      <h3>暂无阵容情报</h3>
      <p>预计首发、正式首发、伤停会分别标记来源和更新时间。</p>
    </div>
    <section class="panel">
      <div class="panel-heading"><h2>情报记录</h2></div>
      <div class="news-item" v-if="match.isDemo">
        <span class="muted mono">10-28 16:20</span>
        <div>
          <strong>赛前训练情况待复核</strong>
          <p>边路人员可用性可能影响阵型安排，当前保留两种阵型情景。</p>
          <span class="small muted">示例情报 · 未连接真实新闻源</span>
        </div>
        <t-tag theme="warning" variant="light">待确认</t-tag>
      </div>
      <div v-else class="section-note">暂无可追溯来源的情报。</div>
    </section>
  </template>
  <template v-else-if="tab === 'odds'">
    <section class="panel">
      <div class="panel-heading">
        <t-radio-group
          v-model="oddsSource"
          variant="default-filled"
          :options="[
            { label: '体彩 SP', value: 'sporttery' },
            { label: '外盘参考', value: 'foreign' },
          ]"
        /><t-checkbox v-model="autoRefresh">每60秒刷新</t-checkbox>
      </div>
      <template v-if="oddsSource === 'sporttery'"
        ><div class="market-tabs">
          <t-radio-group
            v-model="market"
            variant="outline"
            :options="
              Object.entries(marketNames).map(([value, label]) => ({
                value,
                label,
              }))
            "
          />
        </div>
        <t-table
          row-key="option"
          :data="oddsRows"
          :columns="oddsCols"
          empty="该玩法暂无开售数据或尚未接入。"
          ><template #initial="{ row }"
            ><span class="mono muted">{{ price(row.initial) }}</span></template
          ><template #current="{ row }"
            ><strong class="mono">{{ price(row.current) }}</strong></template
          ><template #change="{ row }"
            ><span
              class="mono"
              :class="row.change < 0 ? 'down' : row.change > 0 ? 'up' : 'muted'"
              >{{
                row.change == null
                  ? "—"
                  : row.change === 0
                    ? "持平"
                    : `${row.change > 0 ? "↑ +" : "↓ "}${row.change.toFixed(2)}`
              }}</span
            ></template
          ></t-table
        >
        <div class="section-note">
          {{
            match.isDemo
              ? "示例固定奖金，非实际开售数据"
              : "来源：sporttery.cn"
          }}{{ market === "crs" ? " · 当前仅展示部分比分选项" : "" }} ·
          初始值缺失不补零
        </div></template
      >
      <template v-else
        ><t-table
          row-key="company"
          :data="match.foreign"
          :columns="[
            { colKey: 'company', title: '来源' },
            { colKey: 'home', title: '主胜' },
            { colKey: 'draw', title: '平局' },
            { colKey: 'away', title: '客胜' },
            { colKey: 'time', title: '更新时间' },
          ]"
          empty="外盘数据源尚未接入。"
        />
        <div class="section-note">
          外盘仅用于市场对照，与体彩 SP 独立展示。
        </div></template
      >
    </section>
    <section v-if="oddsSource === 'sporttery'" class="panel mt16">
      <div class="panel-heading">
        <h2>SP 变化</h2>
        <div class="actions">
          <t-radio-group
            v-model="chartRange"
            size="small"
            variant="default-filled"
            :options="[
              { label: '近6小时', value: '6' },
              { label: '近24小时', value: '24' },
            ]"
          /><t-button
            variant="text"
            :disabled="!history.length"
            @click="historical = true"
            >历史明细</t-button
          >
        </div>
      </div>
      <template v-if="history.length"
        ><div class="chart-legend">
          <span><i class="legend-dot blue"></i>主胜</span
          ><span><i class="legend-dot gray"></i>平局</span
          ><span><i class="legend-dot teal"></i>客胜</span
          ><span class="muted">当前 {{ marketNames[market] }} · 示例</span>
        </div>
        <div class="odds-chart">
          <svg
            viewBox="0 0 760 220"
            role="img"
            aria-label="主胜 SP 逐步下降，平局与客胜上升，详细数值可打开历史明细"
          >
            <path
              v-for="y in [40, 90, 140, 190]"
              :key="y"
              :d="`M40 ${y}H720`"
              stroke="#e7e9ed"
            />
            <polyline
              v-for="(key, i) in ['h', 'd', 'a']"
              :key="key"
              :points="linePoints(key)"
              fill="none"
              :stroke="['#0052d9', '#798391', '#16858a'][i]"
              stroke-width="2.5"
            />
            <text
              v-for="(h, i) in history"
              :key="h.time"
              :x="40 + (i * 680) / Math.max(history.length - 1, 1)"
              y="215"
              text-anchor="middle"
              fill="#626b77"
              font-size="12"
            >
              {{ h.time }}
            </text>
          </svg>
        </div></template
      >
      <div v-else class="empty-state compact">
        <p>该玩法暂无历史快照，无法绘制走势。</p>
      </div>
    </section>
  </template>
  <template v-else-if="tab === 'analysis'">
    <div class="analysis-toolbar">
      <span class="muted">{{
        match.isDemo ? "示例模型 · 预计阵容版" : "现有接口 · 市场定价分析"
      }}</span>
      <div class="actions">
        <t-select
          v-model="versionId"
          placeholder="最新版本"
          :options="
            versions.map((v) => ({
              label: `v${v.version} · ${new Date(v.created).toLocaleTimeString('zh-CN', { hour12: false })}`,
              value: v.id,
            }))
          "
          style="width: 190px"
          aria-label="分析版本"
        /><t-button
          variant="outline"
          :disabled="!analysis"
          @click="copyAnalysis"
          ><template #icon><CopyIcon /></template>复制</t-button
        ><t-button
          :loading="analysisBusy"
          :disabled="match.sale !== 'selling'"
          @click="generate"
          >{{ mode === "demo" ? "生成示例版本" : "更新分析" }}</t-button
        >
      </div>
    </div>
    <t-alert
      v-if="analysisError"
      theme="error"
      :message="analysisError"
      class="mb16"
    />
    <template v-if="analysis"
      ><section class="panel analysis-summary">
        <div class="label-caption">
          分析结论 <span class="muted">/ v{{ analysis.version }}</span>
        </div>
        <h2>{{ analysis.title }}</h2>
        <p>{{ analysis.summary }}</p>
        <div class="prediction-grid">
          <div>
            <span class="label-caption">{{ analysis.probLabel }}</span>
            <div class="prob-values">
              <span v-for="(v, i) in analysis.probabilities" :key="i"
                ><small>{{ ["主胜", "平局", "客胜"][i] }}</small
                ><strong
                  >{{ Number(v).toFixed(1) }}<small>%</small></strong
                ></span
              >
            </div>
            <div class="prob-bar">
              <span
                v-for="(v, i) in analysis.probabilities"
                :key="i"
                :style="{
                  width: v + '%',
                  background: ['#0052d9', '#b8c0cc', '#19858a'][i],
                }"
              ></span>
            </div>
          </div>
          <div>
            <span class="label-caption">进球区间</span
            ><strong class="prediction-value">{{ analysis.goals }}</strong>
          </div>
          <div>
            <span class="label-caption">参考比分</span
            ><strong class="prediction-value">{{ analysis.scores }}</strong>
          </div>
        </div>
      </section>
      <div class="two-column detail-grid">
        <section class="panel">
          <div class="panel-heading">
            <h2>判断依据</h2>
            <span class="small muted">点击查看相关数据</span>
          </div>
          <button
            class="evidence-row"
            v-for="(e, i) in analysis.evidence"
            :key="i"
            @click="emit('navigate', `/match/${match.id}/${e.tab}`)"
          >
            <span class="evidence-number">{{
              String(i + 1).padStart(2, "0")
            }}</span
            ><span
              ><strong>{{ e.title }}</strong>
              <p>{{ e.text }}</p></span
            ><ChevronRightIcon />
          </button>
          <div v-if="!analysis.evidence.length" class="section-note">
            接口未提供分析依据。
          </div>
        </section>
        <section class="panel">
          <div class="panel-heading"><h2>关键风险</h2></div>
          <ul class="risk-list">
            <li v-for="r in analysis.risks" :key="r">{{ r }}</li>
          </ul>
          <div v-if="analysis.tactics" class="context-callout">
            <span class="label-caption">对位观察</span>
            <p>{{ analysis.tactics }}</p>
          </div>
          <div class="section-note">
            {{
              analysis.isDemo
                ? "以上均为示例结果。"
                : "市场隐含概率来自固定奖金计算，不是训练模型预测。"
            }}
          </div>
        </section>
      </div>
    </template>
    <section v-else class="panel empty-state">
      <h3>这场比赛还没有分析</h3>
      <p>点击“更新分析”读取现有分析接口，并保存不可覆盖的版本快照。</p>
    </section>
    <section class="panel mt16">
      <div class="panel-heading">
        <h2>我的笔记</h2>
        <span class="small muted">仅保存在当前浏览器</span>
      </div>
      <div class="note-editor">
        <t-textarea
          v-model="noteDraft"
          placeholder="记录自己的判断、需要核实的消息或赛后观察…"
          :maxlength="2000"
          :autosize="{ minRows: 3, maxRows: 8 }"
          aria-label="比赛分析笔记"
        />
        <div class="note-footer">
          <span class="small muted">{{
            noteDraft === note ? "已保存" : "有未保存的修改"
          }}</span
          ><t-button :disabled="noteDraft === note" @click="saveNote"
            >保存笔记</t-button
          >
        </div>
      </div>
    </section>
  </template>
  <div class="page-footnote">
    {{ match.isDemo ? "示例数据用于产品确认" : "数据以来源最新状态为准" }} ·
    分析版本保留生成时间，后续更新不覆盖旧版本。
  </div>
  <t-drawer
    v-model:visible="historical"
    header="SP 历史明细"
    size="540px"
    :footer="false"
    :close-on-esc-keydown="true"
    @close="historical = false"
    ><t-table row-key="time" :data="history" :columns="historyCols" />
    <p class="section-note">示例数据 · 北京时间</p></t-drawer
  >
  <t-dialog v-model:visible="copyOpen" header="复制分析" :footer="false"
    ><p>浏览器未允许自动复制，请选中文本复制。</p>
    <t-textarea
      :value="copyText"
      :autosize="{ minRows: 8, maxRows: 14 }"
      readonly
  /></t-dialog>
</template>

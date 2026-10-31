<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { MessagePlugin } from "tdesign-vue-next";
import {
  ArrowLeftIcon,
  RefreshIcon,
  StarIcon,
  StarFilledIcon,
  ChevronRightIcon,
  CopyIcon,
} from "tdesign-icons-vue-next";
import { api, timeLabel, lifecycleNames } from "./api";
import { price, marketNames } from "./domain";
const props = defineProps({
  match: Object,
  tab: String,
  state: Object,
  aiConfigured: Boolean,
});
const emit = defineEmits(["navigate", "favorite", "saved"]);
const detail = ref(null),
  loading = ref(false),
  error = ref(""),
  scope = ref("all"),
  market = ref("had"),
  oddsSource = ref("sporttery"),
  chartRange = ref("all"),
  historical = ref(false),
  busy = ref(false),
  analysisError = ref(""),
  versionId = ref(""),
  copyOpen = ref(false),
  copyText = ref(""),
  saving = ref(false),
  autoRefresh = ref(true);
const evidenceOpen = ref(false),
  evidenceSaving = ref(false),
  evidenceError = ref("");
const evidenceDraft = ref({
  kind: "fact",
  text: "",
  source_url: "",
  published_at: "",
});
async function saveEvidence() {
  evidenceSaving.value = true;
  evidenceError.value = "";
  try {
    const d = evidenceDraft.value;
    if (!d.text.trim() || !d.source_url.trim() || !d.published_at)
      throw new Error("请填写内容、来源链接和发布时间");
    const published = new Date(d.published_at);
    if (!Number.isFinite(published.getTime()))
      throw new Error("发布时间格式无效");
    await api.evidence(props.match.id, {
      ...d,
      published_at: published.toISOString(),
    });
    evidenceOpen.value = false;
    evidenceDraft.value = {
      kind: "fact",
      text: "",
      source_url: "",
      published_at: "",
    };
    await load();
    MessagePlugin.success("已保存情报");
  } catch (e) {
    evidenceError.value = e.message;
  } finally {
    evidenceSaving.value = false;
  }
}
const draftKey = `football:draft:${props.match.id}`;
const noteDraft = ref(
  sessionStorage.getItem(draftKey) ?? props.state.notes[props.match.id] ?? "",
);
watch(noteDraft, (v) => sessionStorage.setItem(draftKey, v));
watch(
  () => props.state.notes[props.match.id],
  (v, old) => {
    if (noteDraft.value === (old || "")) noteDraft.value = v || "";
  },
);
const tabs = [
  ["basic", "基本面"],
  ["lineups", "阵容情报"],
  ["odds", "赔率"],
  ["analysis", "预测分析"],
];
const context = computed(() => detail.value?.context || {});
const versions = computed(() => {
  const all = [
    ...(props.state.versions[props.match.id] || []),
    ...(detail.value?.versions || []),
  ];
  return [...new Map(all.map((v) => [v.id, v])).values()].sort(
    (a, b) => b.version - a.version,
  );
});
const analysis = computed(
  () =>
    versions.value.find((v) => v.id === versionId.value) || versions.value[0],
);
const form = computed(() =>
  [0, 1].map((i) =>
    (context.value.recent?.[i] || []).filter(
      (r) => scope.value === "all" || r.venue === (i === 0 ? "主" : "客"),
    ),
  ),
);
const basicStats = computed(() => {
  const stats = form.value.map((rows) => ({
    n: rows.length,
    w: rows.filter((r) => r.gf > r.ga).length,
    d: rows.filter((r) => r.gf === r.ga).length,
    l: rows.filter((r) => r.gf < r.ga).length,
    g: rows.length
      ? (rows.reduce((s, r) => s + r.gf, 0) / rows.length).toFixed(2)
      : "—",
    a: rows.length
      ? (rows.reduce((s, r) => s + r.ga, 0) / rows.length).toFixed(2)
      : "—",
  }));
  return [
    ["样本场次", ...stats.map((s) => `${s.n} 场`)],
    ["近期战绩", ...stats.map((s) => (s.n ? `${s.w}胜${s.d}平${s.l}负` : "—"))],
    ["场均进球", ...stats.map((s) => s.g)],
    ["场均失球", ...stats.map((s) => s.a)],
    ["体彩排名", props.match.home_rank || "—", props.match.away_rank || "—"],
  ];
});
const formCols = [
  { colKey: "date", title: "日期", width: 106 },
  { colKey: "opponent", title: "对手", minWidth: 160 },
  { colKey: "venue", title: "主客", width: 60 },
  { colKey: "score", title: "比分", width: 66 },
  { colKey: "result", title: "赛果", width: 58 },
];
function formRows(i) {
  return form.value[i].map((r) => ({
    ...r,
    date: r.date.slice(0, 10),
    result: r.gf > r.ga ? "胜" : r.gf === r.ga ? "平" : "负",
  }));
}
const markets = computed(() => detail.value?.markets || props.match.pools);
const fullHistory = computed(() => detail.value?.history?.[market.value] || []);
const history = computed(() =>
  fullHistory.value.filter(
    (r) =>
      chartRange.value === "all" ||
      Date.parse(r.updated_at) >=
        Date.now() - Number(chartRange.value) * 3600000,
  ),
);
const oddsRows = computed(() => {
  const p = markets.value[market.value];
  const first = fullHistory.value[0];
  return (p?.options || []).map((o) => {
    const initial = first?.options.find((i) => i.key === o.key)?.odds;
    return {
      option: o.label,
      initial,
      current: o.odds,
      change: initial == null ? null : o.odds - initial,
      updated: timeLabel(p.updated_at),
    };
  });
});
const oddsCols = [
  { colKey: "option", title: "选项", minWidth: 125 },
  { colKey: "initial", title: "最早可得 SP", width: 130 },
  { colKey: "current", title: "最新 SP", width: 115 },
  { colKey: "change", title: "较最早记录", width: 120 },
  { colKey: "updated", title: "来源更新时间", width: 215 },
];
const chartHistory = computed(() =>
  ["had", "hhad"].includes(market.value)
    ? history.value.filter((h) =>
        ["h", "d", "a"].every((k) => h.options.some((o) => o.key === k)),
      )
    : [],
);
const chartBounds = computed(() => {
  const values = chartHistory.value.flatMap((h) =>
    h.options.map((o) => o.odds),
  );
  return {
    lo: Math.max(0, Math.min(...values) - 0.2),
    hi: Math.max(...values) + 0.2,
  };
});
function linePoints(key) {
  const rows = chartHistory.value;
  const first = Date.parse(rows[0].updated_at),
    last = Date.parse(rows.at(-1).updated_at);
  return rows
    .map(
      (h) =>
        `${48 + ((Date.parse(h.updated_at) - first) / Math.max(last - first, 1)) * 660},${180 - ((h.options.find((o) => o.key === key).odds - chartBounds.value.lo) / (chartBounds.value.hi - chartBounds.value.lo)) * 140}`,
    )
    .join(" ");
}
const historyRows = computed(() =>
  [...history.value]
    .reverse()
    .slice(0, 60)
    .map((r) => ({
      time: timeLabel(r.updated_at),
      prices: r.options.map((o) => `${o.label} ${price(o.odds)}`).join("　"),
    })),
);
const followed = computed(() => props.state.favorites.includes(props.match.id));
const predictionBlocked = computed(
  () =>
    Date.parse(props.match.kickoff) <= Date.now() ||
    props.match.stale ||
    detail.value?.quality?.can_predict === false ||
    detail.value?.cancelled ||
    ["live", "completed"].includes(context.value.state),
);
async function load(force = false) {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const d = await api.detail(props.match.id, force);
    detail.value = d.detail;
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
async function generate(narrative = false) {
  busy.value = true;
  analysisError.value = "";
  try {
    const data = await api.predict(props.match.id, narrative);
    detail.value = data.detail;
    versionId.value = data.prediction.id;
    emit("saved");
    MessagePlugin.success("预测与输入快照已保存");
  } catch (e) {
    analysisError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function saveNote() {
  saving.value = true;
  try {
    await api.save("notes", props.match.id, noteDraft.value);
    sessionStorage.removeItem(draftKey);
    emit("saved");
    MessagePlugin.success("笔记已保存至数据库");
  } catch (e) {
    MessagePlugin.error(e.message);
  } finally {
    saving.value = false;
  }
}
async function copy() {
  const a = analysis.value;
  if (!a) return;
  const text = `${props.match.home} vs ${props.match.away}\n${props.match.kickoff}\n${a.title}\n${a.narrative || a.evidence.map((e) => e.text).join("\n")}\n风险：${a.risks.join("；")}\n${a.engine} · v${a.version} · ${timeLabel(a.created)}`;
  try {
    await navigator.clipboard.writeText(text);
    MessagePlugin.success("已复制分析");
  } catch {
    copyText.value = text;
    copyOpen.value = true;
  }
}
function restDays(i) {
  const recent = context.value.recent?.[i];
  if (!recent?.length) return "—";
  return `${Math.max(0, Math.floor((Date.parse(props.match.kickoff) - Date.parse(recent[0].date)) / 86400000))} 天`;
}
let timer;
onMounted(() => {
  load();
  timer = setInterval(() => {
    if (autoRefresh.value && !document.hidden && !busy.value) load();
  }, 60000);
});
onBeforeUnmount(() => clearInterval(timer));
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
          >{{ match.kickoff.slice(0, 10) }} {{ match.time }} · 北京时间</span
        ><span>{{ match.num }}</span
        ><span>{{
          context.score || lifecycleNames[context.state || match.lifecycle]
        }}</span>
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
      ><t-button variant="outline" :loading="loading" @click="load(true)"
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
  <t-alert v-if="error" theme="error" :message="error" class="mb16" /><t-alert
    v-if="detail?.errors?.length"
    theme="warning"
    :message="detail.errors.join('；')"
    class="mb16"
  />
  <div v-if="loading && !detail" class="panel empty-state">
    <h3>正在核对比赛与采集数据…</h3>
  </div>
  <template v-else-if="tab === 'basic'"
    ><div class="two-column detail-grid">
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
        <div class="compare-header">
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
            context.available
              ? "来源：ESPN 已核实赛果；排名来自体彩"
              : "近期赛果不可用：" + (context.reason || "尚未核实")
          }}{{ scope === "venue" ? " · 在最近样本内筛选主 / 客场" : "" }}
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
            <dt>场地</dt>
            <dd>{{ context.venue || "—" }}</dd>
          </div>
          <div>
            <dt>距上一场</dt>
            <dd>主队 {{ restDays(0) }} / 客队 {{ restDays(1) }}</dd>
          </div>
          <div>
            <dt>正式首发</dt>
            <dd>
              {{
                context.lineups?.length === 2 &&
                context.lineups.every((r) => r.status === "confirmed")
                  ? "双方已确认"
                  : "待确认"
              }}
            </dd>
          </div>
          <div>
            <dt>伤停</dt>
            <dd>{{ context.injuries_verified ? "已核实" : "未获可靠确认" }}</dd>
          </div>
          <div>
            <dt>基本面采集</dt>
            <dd>{{ timeLabel(context.captured_at) }}</dd>
          </div>
        </dl>
        <div class="section-note">
          <a
            v-if="context.source_url"
            :href="context.source_url"
            target="_blank"
            rel="noopener noreferrer"
            >查看 ESPN 来源 ↗</a
          >
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
          empty="暂无通过匹配核验的赛果"
          ><template #result="{ row }"
            ><span
              class="result-square"
              :class="{ win: row.result === '胜', loss: row.result === '负' }"
              >{{ row.result }}</span
            ></template
          ></t-table
        >
      </section>
    </div></template
  >
  <template v-else-if="tab === 'lineups'"
    ><div class="two-column detail-grid">
      <section
        class="panel"
        v-for="(team, i) in [match.home, match.away]"
        :key="team"
      >
        <div class="panel-heading">
          <h2>{{ team }}</h2>
          <t-tag
            :theme="
              context.lineups?.[i]?.status === 'confirmed'
                ? 'success'
                : 'default'
            "
            variant="light"
            >{{
              context.lineups?.[i]?.status === "confirmed"
                ? "首发已确认"
                : "首发待确认"
            }}</t-tag
          >
        </div>
        <t-table
          row-key="name"
          :data="context.lineups?.[i]?.players || []"
          :columns="[
            { colKey: 'number', title: '号码', width: 80 },
            { colKey: 'name', title: '球员', minWidth: 170 },
            { colKey: 'position', title: '位置', width: 100 },
          ]"
          empty="正式首发尚未公布或尚未获取"
        />
        <div class="section-note">
          {{
            context.lineups?.[i]?.formation
              ? "阵型 " + context.lineups[i].formation
              : "阵型尚未确认"
          }}
        </div>
      </section>
    </div>
    <section class="panel mt16">
      <div class="panel-heading">
        <h2>伤停与情报</h2>
        <t-button variant="outline" @click="evidenceOpen = true"
          >添加情报</t-button
        >
      </div>
      <div
        v-for="e in detail?.evidence || []"
        :key="e.id"
        class="source-evidence"
      >
        <div class="actions">
          <t-tag variant="light">{{
            e.kind === "fact" ? "事实记录" : "外部观点"
          }}</t-tag
          ><span class="small muted">{{
            e.published_precision === "date"
              ? e.published_at.slice(0, 10)
              : timeLabel(e.published_at)
          }}</span
          ><a :href="e.source_url" target="_blank" rel="noopener noreferrer"
            >来源 ↗</a
          >
        </div>
        <p>{{ e.text }}</p>
      </div>
      <div v-if="!detail?.evidence?.length" class="empty-state compact">
        <p>伤停待核实。</p>
      </div>
      <div class="section-note">
        {{
          context.source_url
            ? "首发来源：ESPN · " + timeLabel(context.captured_at)
            : "当前赛事尚未获得匹配的阵容来源"
        }}
      </div>
    </section></template
  >
  <template v-else-if="tab === 'odds'"
    ><section class="panel">
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
        <div class="market-meta section-note">
          {{
            markets[market]?.single
              ? "该玩法支持单关"
              : "单关资格未确认或不支持"
          }}<span v-if="markets[market]?.line != null">
            · 主队让球 {{ markets[market].line > 0 ? "+" : ""
            }}{{ markets[market].line }}</span
          >
        </div>
        <t-table
          row-key="option"
          :data="oddsRows"
          :columns="oddsCols"
          empty="该玩法暂无可用固定奖金"
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
                  : Math.abs(row.change) < 0.001
                    ? "持平"
                    : `${row.change > 0 ? "↑ +" : "↓ "}${row.change.toFixed(2)}`
              }}</span
            ></template
          ></t-table
        >
        <div class="section-note">
          体彩官方 · 核验于
          {{ timeLabel(detail?.quality?.verified_at || detail?.captured_at) }}
          <t-tag
            v-if="detail?.quality?.can_predict === false"
            theme="warning"
            variant="light"
            >{{ detail.quality.reasons.join("；") }}</t-tag
          >
        </div></template
      >
      <div v-else class="empty-state compact">
        <h3>暂无已匹配的外盘数据</h3>
        <p>数据源未配置</p>
      </div>
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
              { label: '6小时', value: '6' },
              { label: '24小时', value: '24' },
              { label: '全部', value: 'all' },
            ]"
          /><t-button
            variant="text"
            :disabled="!history.length"
            @click="historical = true"
            >历史明细</t-button
          >
        </div>
      </div>
      <template v-if="chartHistory.length > 1"
        ><div class="chart-legend">
          <span><i class="legend-dot blue"></i>主胜</span
          ><span><i class="legend-dot gray"></i>平局</span
          ><span><i class="legend-dot teal"></i>客胜</span>
        </div>
        <div class="odds-chart">
          <svg
            viewBox="0 0 760 220"
            role="img"
            aria-label="SP 历史折线，精确数值可查看历史明细"
          >
            <g v-for="(y, i) in [40, 90, 140, 180]" :key="y">
              <path :d="`M48 ${y}H708`" stroke="#e7e9ed" />
              <text
                x="38"
                :y="y + 4"
                text-anchor="end"
                fill="#68717e"
                font-size="11"
              >
                {{
                  (
                    chartBounds.hi -
                    ((y - 40) / 140) * (chartBounds.hi - chartBounds.lo)
                  ).toFixed(2)
                }}
              </text>
            </g>
            <polyline
              v-for="(key, i) in ['h', 'd', 'a']"
              :key="key"
              :points="linePoints(key)"
              fill="none"
              :stroke="['#0052d9', '#798391', '#16858a'][i]"
              stroke-width="2.5"
            />
            <text x="48" y="210" fill="#68717e" font-size="11">
              {{ timeLabel(chartHistory[0].updated_at) }}
            </text>
            <text
              x="708"
              y="210"
              text-anchor="end"
              fill="#68717e"
              font-size="11"
            >
              {{ timeLabel(chartHistory.at(-1).updated_at) }}
            </text>
          </svg>
        </div></template
      >
      <div v-else class="empty-state compact">
        <p>
          {{
            history.length
              ? "该玩法可通过“历史明细”查看完整变化记录。"
              : "所选时段暂无历史记录。"
          }}
        </p>
      </div>
    </section></template
  >
  <template v-else-if="tab === 'analysis'"
    ><div class="analysis-toolbar">
      <span class="muted">{{ analysis?.engine || "尚无预测" }}</span>
      <div class="actions">
        <t-select
          v-model="versionId"
          placeholder="最新版本"
          :options="
            versions.map((v) => ({
              label: `v${v.version} · ${timeLabel(v.created)}`,
              value: v.id,
            }))
          "
          style="width: 240px"
          aria-label="分析版本"
        /><t-button variant="outline" :disabled="!analysis" @click="copy"
          ><template #icon><CopyIcon /></template>复制</t-button
        ><t-button
          variant="outline"
          :disabled="!aiConfigured || predictionBlocked || busy"
          @click="generate(true)"
          >AI 解读</t-button
        ><t-button
          :loading="busy"
          :disabled="predictionBlocked || loading"
          @click="generate(false)"
          >{{ analysis ? "更新预测" : "生成预测" }}</t-button
        >
      </div>
    </div>
    <t-alert
      v-if="analysisError"
      theme="error"
      :message="analysisError"
      class="mb16"
    />
    <div v-if="predictionBlocked" class="inline-notice">
      无法生成赛前预测，可查看历史版本。
    </div>
    <template v-if="analysis"
      ><section class="panel analysis-summary">
        <div class="label-caption">
          预测结论 / v{{ analysis.version }} · {{ timeLabel(analysis.created) }}
        </div>
        <h2>{{ analysis.title }}</h2>
        <div class="prediction-grid">
          <div>
            <span class="label-caption">{{ analysis.probLabel }}</span>
            <div class="prob-values">
              <span v-for="(v, i) in analysis.probabilities" :key="i"
                ><small>{{ ["主胜", "平局", "客胜"][i] }}</small
                ><strong>{{ v.toFixed(1) }}<small>%</small></strong></span
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
            <span class="label-caption">进球优先观察</span
            ><strong class="prediction-value">{{ analysis.goals }}</strong>
          </div>
          <div>
            <span class="label-caption">比分优先观察</span
            ><strong class="prediction-value">{{ analysis.scores }}</strong>
          </div>
        </div>
      </section>
      <div class="two-column detail-grid">
        <section class="panel">
          <div class="panel-heading">
            <h2>判断依据</h2>
            <span class="small muted">点击核对数据</span>
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
        </section>
        <section class="panel">
          <div class="panel-heading"><h2>关键风险</h2></div>
          <ul class="risk-list">
            <li v-for="r in analysis.risks" :key="r">{{ r }}</li>
          </ul>
          <div class="section-note">
            计算版本：{{ analysis.model_version }}<br />未校准
          </div>
        </section>
      </div>
      <section v-if="analysis.narrative" class="panel mt16">
        <div class="panel-heading">
          <h2>AI 解读</h2>
          <span class="small muted">基于该版本数据</span>
        </div>
        <div class="narrative-text">
          {{ analysis.narrative.replaceAll("**", "") }}
        </div>
      </section></template
    >
    <section v-else class="panel empty-state">
      <h3>尚无赛前预测</h3>
    </section>
    <div v-if="!aiConfigured" class="section-note">AI 解读未配置。</div>
    <section class="panel mt16">
      <div class="panel-heading">
        <h2>我的笔记</h2>
      </div>
      <div class="note-editor">
        <t-textarea
          v-model="noteDraft"
          placeholder="记录自己的判断、需要核实的消息或赛后观察…"
          :maxlength="2000"
          :rows="3"
          aria-label="比赛分析笔记"
        />
        <div class="note-footer">
          <span class="small muted">{{
            noteDraft === (state.notes[match.id] || "")
              ? "已保存"
              : "有未保存的修改"
          }}</span
          ><t-button
            :loading="saving"
            :disabled="noteDraft === (state.notes[match.id] || '')"
            @click="saveNote"
            >保存笔记</t-button
          >
        </div>
      </div>
    </section></template
  >
  <t-drawer
    v-model:visible="historical"
    header="SP 历史明细"
    size="680px"
    :footer="false"
    :close-on-esc-keydown="true"
    @close="historical = false"
    ><t-table
      row-key="time"
      :data="historyRows"
      :columns="[
        { colKey: 'time', title: '来源时间', width: 180 },
        { colKey: 'prices', title: '固定奖金', minWidth: 350 },
      ]"
    />
    <p class="section-note">
      {{ marketNames[market] }} · 最近60条可得记录 · 北京时间
    </p></t-drawer
  >
  <t-dialog v-model:visible="copyOpen" header="复制分析" :footer="false"
    ><p>请选中文本复制。</p>
    <t-textarea :value="copyText" :rows="8" readonly
  /></t-dialog>
  <t-dialog
    v-model:visible="evidenceOpen"
    header="添加情报"
    :footer="false"
    :close-on-overlay-click="false"
  >
    <t-alert
      v-if="evidenceError"
      theme="error"
      :message="evidenceError"
      class="mb16"
    />
    <t-form label-align="top" @submit.prevent="saveEvidence">
      <t-form-item label="类型"
        ><t-radio-group
          v-model="evidenceDraft.kind"
          :options="[
            { label: '事实记录', value: 'fact' },
            { label: '外部观点', value: 'opinion' },
          ]"
      /></t-form-item>
      <t-form-item label="内容"
        ><t-textarea
          v-model="evidenceDraft.text"
          :maxlength="1500"
          :autosize="{ minRows: 3, maxRows: 8 }"
      /></t-form-item>
      <t-form-item label="来源链接"
        ><t-input
          v-model="evidenceDraft.source_url"
          type="url"
          placeholder="https://"
      /></t-form-item>
      <t-form-item label="发布时间（北京时间）"
        ><t-input
          v-model="evidenceDraft.published_at"
          placeholder="2026-10-31T14:00:00+08:00"
      /></t-form-item>
      <div class="actions">
        <t-button :loading="evidenceSaving" @click="saveEvidence"
          >保存情报</t-button
        ><t-button variant="outline" @click="evidenceOpen = false"
          >取消</t-button
        >
      </div>
    </t-form>
  </t-dialog>
</template>

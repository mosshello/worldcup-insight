<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from "vue";
import { api, timeLabel } from "./api";
import { marketNames, price } from "./domain";
import {
  movement,
  optionValue,
  trendRows,
  quoteTime,
  quoteInterval,
  relativeMovement,
  kickoffDistance,
} from "./oddsTrend";
const props = defineProps({ matchId: String });
const data = ref(null),
  error = ref(""),
  loading = ref(false),
  code = ref("had"),
  mode = ref("odds"),
  range = ref("all"),
  automatic = ref(true),
  keys = ref([]),
  active = ref(""),
  shown = ref(20);
let timer,
  seq = 0;
async function load() {
  if (loading.value) return;
  const token = ++seq;
  loading.value = true;
  try {
    const r = await api.odds(props.matchId);
    if (token === seq) {
      data.value = r.odds;
      error.value = "";
    }
  } catch (e) {
    if (token === seq) error.value = e.message;
  } finally {
    if (token === seq) loading.value = false;
  }
}
const pool = computed(() => data.value?.markets?.[code.value] || {});
const options = computed(() => pool.value.options || []);
watch([code, () => options.value.map((o) => o.key).join(",")], () => {
  keys.value = [...options.value]
    .sort((a, b) => a.odds - b.odds)
    .slice(0, 3)
    .map((o) => o.key);
  shown.value = 20;
  active.value = "";
});
const rows = computed(() =>
  trendRows(
    data.value?.history?.[code.value],
    pool.value,
    code.value,
    range.value,
  ),
);
const allRows = computed(() =>
  trendRows(data.value?.history?.[code.value], pool.value, code.value),
);
const selected = computed(() =>
  options.value.filter((o) => keys.value.includes(o.key)),
);
const colors = ["#d84b65", "#168ac4", "#16865e", "#9b6bcc", "#bf7b22"];
const values = computed(() =>
  rows.value
    .flatMap((p) => keys.value.map((k) => optionValue(p, k, mode.value)))
    .filter(Number.isFinite),
);
const bounds = computed(() => {
  const vals = values.value.length ? values.value : [0, 1],
    min = Math.min(...vals),
    max = Math.max(...vals),
    pad = Math.max((max - min) * 0.15, 0.2);
  return { lo: Math.max(0, min - pad), hi: max + pad };
});
const point = (r, k) => {
  const first = Date.parse(rows.value[0]?.updated_at),
    last = Date.parse(rows.value.at(-1)?.updated_at),
    value = optionValue(r, k, mode.value);
  return {
    x:
      rows.value.length === 1
        ? 427
        : 90 +
          ((Date.parse(r.updated_at) - first) / Math.max(last - first, 1)) *
            674,
    y:
      230 -
      ((value - bounds.value.lo) / (bounds.value.hi - bounds.value.lo)) * 195,
    value,
  };
};
function path(key) {
  let d = "",
    gap = true;
  for (const r of rows.value) {
    const p = point(r, key);
    if (p.value == null) {
      gap = true;
      continue;
    }
    d += `${gap ? "M" : "L"}${p.x},${p.y} `;
    gap = false;
  }
  return d;
}
function toggle(key) {
  keys.value = keys.value.includes(key)
    ? keys.value.filter((k) => k !== key)
    : [...keys.value.slice(-4), key];
}
const number = (v) =>
  Number.isFinite(v)
    ? v.toFixed(2) + (mode.value === "probability" ? "%" : "")
    : "—";
const difference = (r, k) => {
  const index = allRows.value.findIndex((p) => p.updated_at === r.updated_at);
  return movement(
    optionValue(r, k, mode.value),
    optionValue(allRows.value[index - 1], k, mode.value),
  );
};
const lastRows = computed(() =>
  [...rows.value].reverse().slice(0, shown.value),
);
const inspected = computed(
  () =>
    rows.value.find((r) => r.updated_at === active.value) || rows.value.at(-1),
);
const interval = (r) => quoteInterval(allRows.value, r);
const previousValue = (r, k) =>
  optionValue(interval(r).previous, k, mode.value);
const relative = (r, k) =>
  relativeMovement(
    optionValue(r, k, mode.value),
    previousValue(r, k),
    mode.value,
  );
const historyStyle = computed(() => ({
  "--quote-columns": Math.max(selected.value.length, 1),
}));
const chartTicks = computed(() => {
  if (!rows.value.length) return [];
  const first = Date.parse(rows.value[0].updated_at),
    last = Date.parse(rows.value.at(-1).updated_at);
  return Array.from({ length: first === last ? 1 : 5 }, (_, i) => ({
    x: first === last ? 427 : 90 + (i * 674) / 4,
    time: new Date(first + ((last - first) * i) / 4).toISOString(),
  }));
});
function selectRecord(r) {
  active.value = r.updated_at;
  const index = [...rows.value]
    .reverse()
    .findIndex((p) => p.updated_at === r.updated_at);
  shown.value = Math.max(shown.value, index + 1);
}
watch(range, () => {
  active.value = "";
  shown.value = 20;
});
onMounted(() => {
  load();
  timer = setInterval(() => {
    if (automatic.value && !document.hidden) load();
  }, 30000);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  seq++;
});
</script>
<template>
  <section class="official-odds" aria-label="官方赔率走势">
    <div class="quote-toolbar">
      <div class="quote-tabs" aria-label="赔率玩法">
        <button
          v-for="(name, k) in marketNames"
          :key="k"
          :aria-pressed="code === k"
          @click="code = k"
        >
          {{ name }}
        </button>
      </div>
      <div class="quote-controls">
        <label
          ><input v-model="automatic" type="checkbox" /> 自动刷新（30秒）</label
        ><t-button variant="outline" :loading="loading" @click="load"
          >刷新赔率</t-button
        >
      </div>
    </div>
    <p v-if="error || data?.error" role="alert" class="quote-warning">
      {{ error || data.error }} · 保留上次成功数据及原时间
    </p>
    <div class="quote-status">
      <a :href="data?.source_url" target="_blank" rel="noopener noreferrer"
        >体彩官方</a
      ><span :class="{ 'quote-warning': data?.stale }">{{
        data?.closed
          ? "已开赛或停售 · 最后报价"
          : data?.stale
            ? "数据已陈旧 · 等待更新"
            : data?.sale_status === "selling"
              ? "在售报价"
              : "销售状态待核实"
      }}</span
      ><span>列表核验 {{ timeLabel(data?.checked_at) }}</span
      ><span>走势核验 {{ timeLabel(data?.history_checked_at) }}</span
      ><span v-if="code === 'hhad'"
        >主队让球 {{ pool.line > 0 ? "+" : ""
        }}{{ pool.line ?? "待核实" }}</span
      >
    </div>
    <div class="quote-options" aria-label="最新赔率">
      <div v-for="o in options" :key="o.key" class="quote-option">
        <span>{{ o.label }}</span
        ><strong>{{ price(o.odds) }}</strong
        ><span
          :class="
            movement(
              o.odds,
              allRows[0]?.options.find((p) => p.key === o.key)?.odds,
            ).direction
          "
          >{{
            movement(
              o.odds,
              allRows[0]?.options.find((p) => p.key === o.key)?.odds,
            ).text
          }}
          <small>较最早</small></span
        >
      </div>
    </div>
    <p class="quote-note">
      最近调价 {{ timeLabel(pool.updated_at) }} ·
      核验时间表示刚检查过官网，调价时间只有官网改价才变化。
    </p>
    <div class="quote-toolbar chart-title">
      <h2>赛前赔率走势</h2>
      <div class="quote-controls">
        <button :aria-pressed="mode === 'odds'" @click="mode = 'odds'">
          赔率</button
        ><button
          :aria-pressed="mode === 'probability'"
          @click="mode = 'probability'"
        >
          隐含概率</button
        ><select v-model="range" aria-label="走势时段">
          <option value="all">全部记录</option>
          <option value="24">近24小时</option>
          <option value="6">近6小时</option>
        </select>
      </div>
    </div>
    <div class="quote-legend" aria-label="走势选项">
      <button
        v-for="o in options"
        :key="o.key"
        :aria-pressed="keys.includes(o.key)"
        @click="toggle(o.key)"
      >
        <i
          :style="{
            background: keys.includes(o.key)
              ? colors[selected.findIndex((p) => p.key === o.key)]
              : '#b8c1cc',
          }"
        />{{ o.label }}
      </button>
    </div>
    <p class="quote-note">
      最多同时对比5项。{{
        mode === "probability"
          ? "隐含概率由本玩法完整SP归一化计算，不是模型预测。"
          : "红色箭头表示赔率升高，绿色表示降低。"
      }}{{
        code === "hhad" ? "仅比较当前让球线；不同让球线不连成同一条走势。" : ""
      }}
    </p>
    <div v-if="rows.length && keys.length" class="quote-chart">
      <svg
        viewBox="0 0 800 290"
        role="img"
        :aria-label="`${marketNames[code]}${mode === 'odds' ? '赔率' : '隐含概率'}走势，${rows.length}条记录`"
      >
        <g v-for="i in 5" :key="i">
          <path :d="`M90 ${35 + (i - 1) * 48.75}H764`" stroke="#e3e9ef" />
          <text x="80" :y="39 + (i - 1) * 48.75" text-anchor="end">
            {{ (bounds.hi - ((i - 1) / 4) * (bounds.hi - bounds.lo)).toFixed(2)
            }}{{ mode === "probability" ? "%" : "" }}
          </text>
        </g>
        <path
          v-if="inspected && selected.length"
          :d="`M${point(inspected, selected[0].key).x} 26V230`"
          stroke="#8da4b7"
          stroke-dasharray="4 4"
        />
        <g v-for="(o, i) in selected" :key="o.key">
          <path
            :d="path(o.key)"
            fill="none"
            :stroke="colors[i]"
            stroke-width="2.5"
          />
          <template v-for="r in rows" :key="r.updated_at">
            <circle
              v-if="point(r, o.key).value != null"
              :cx="point(r, o.key).x"
              :cy="point(r, o.key).y"
              :r="inspected?.updated_at === r.updated_at ? 6 : 4"
              :fill="colors[i]"
              tabindex="0"
              role="button"
              :aria-label="`${quoteTime(r.updated_at)} ${o.label} ${number(point(r, o.key).value)}`"
              @mouseenter="selectRecord(r)"
              @focus="selectRecord(r)"
              @click="selectRecord(r)"
              @keydown.enter="selectRecord(r)"
              @keydown.space.prevent="selectRecord(r)"
            >
              <title>
                {{ quoteTime(r.updated_at) }} · {{ o.label }}
                {{ number(point(r, o.key).value) }} ·
                {{ difference(r, o.key).text }}
              </title>
            </circle>
          </template>
        </g>
        <g
          v-for="(tick, i) in chartTicks"
          :key="tick.time"
          :class="{ 'middle-tick': i > 0 && i < chartTicks.length - 1 }"
        >
          <path :d="`M${tick.x} 230v5`" stroke="#b5c3d0" />
          <text
            :x="tick.x"
            y="253"
            :text-anchor="
              i === 0 ? 'start' : i === chartTicks.length - 1 ? 'end' : 'middle'
            "
          >
            {{ quoteTime(tick.time).split(" ")[0] }}
          </text>
          <text
            :x="tick.x"
            y="271"
            :text-anchor="
              i === 0 ? 'start' : i === chartTicks.length - 1 ? 'end' : 'middle'
            "
          >
            {{ quoteTime(tick.time).split(" ")[1] }}
          </text>
        </g>
      </svg>
    </div>
    <p v-else class="quote-empty">
      {{ loading ? "正在读取官方赔率…" : "所选时段或选项暂无可绘制数据。" }}
    </p>
    <p v-if="rows.length === 1" class="quote-note">
      仅有1条有效记录，暂无连续调价走势。
    </p>
    <section
      v-if="inspected && selected.length"
      class="quote-inspect"
      aria-label="时段涨跌对比"
      :style="historyStyle"
    >
      <div class="inspect-heading">
        <div>
          <strong>{{
            !interval(inspected).previous
              ? "最早取得报价"
              : inspected === rows.at(-1)
                ? "最近一次变化"
                : "选中时段"
          }}</strong>
          <p>{{ interval(inspected).label }}</p>
          <small>{{
            kickoffDistance(data?.fixture?.kickoff, inspected.updated_at)
          }}</small>
        </div>
        <button v-if="active" class="latest-record" @click="active = ''">
          回到最新
        </button>
      </div>
      <div class="inspect-values">
        <div v-for="(o, i) in selected" :key="o.key">
          <span><i :style="{ background: colors[i] }" />{{ o.label }}</span>
          <div class="price-transition">
            <span>{{ number(previousValue(inspected, o.key)) }}</span
            ><span class="transition-arrow">→</span
            ><strong>{{ number(optionValue(inspected, o.key, mode)) }}</strong>
          </div>
          <span :class="difference(inspected, o.key).direction"
            >{{ difference(inspected, o.key).text }}
            <small>{{ relative(inspected, o.key) }}</small></span
          >
        </div>
      </div>
    </section>
    <h3>调价记录 <small>最新在前 · 北京时间</small></h3>
    <p class="quote-note">
      每行对比相邻两次已取得的报价；涨跌以本次减上次计算。悬停图中圆点或点选时间，可查看对应时段。连线不代表中间每分钟都有报价。
    </p>
    <div class="quote-history" :style="historyStyle">
      <div v-if="selected.length && lastRows.length" class="history-header">
        <span>调价时间 / 距开赛</span>
        <div class="history-values">
          <span v-for="o in selected" :key="o.key"
            >{{ o.label
            }}<small>{{
              mode === "odds" ? "赔率 / 较上次" : "概率 / 较上次"
            }}</small></span
          >
        </div>
      </div>
      <article
        v-for="r in lastRows"
        :key="r.updated_at"
        :class="{ 'active-row': inspected?.updated_at === r.updated_at }"
      >
        <div class="history-time">
          <button
            :aria-pressed="inspected?.updated_at === r.updated_at"
            @click="selectRecord(r)"
          >
            <time :datetime="r.updated_at">{{
              quoteTime(r.updated_at)
            }}</time></button
          ><small>{{
            kickoffDistance(data?.fixture?.kickoff, r.updated_at)
          }}</small
          ><small v-if="interval(r).previous"
            >对比 {{ quoteTime(interval(r).previous.updated_at) }}</small
          ><small v-else>最早取得 · 无上一条报价</small>
        </div>
        <div class="history-values">
          <div v-for="o in selected" :key="o.key">
            <span class="mobile-option-label">{{ o.label }}</span
            ><strong>{{ number(optionValue(r, o.key, mode)) }}</strong
            ><small :class="difference(r, o.key).direction">{{
              difference(r, o.key).text
            }}</small
            ><small class="previous-price">{{
              interval(r).previous
                ? "上次 " + number(previousValue(r, o.key))
                : "最早取得"
            }}</small>
          </div>
        </div>
      </article>
    </div>
    <t-button v-if="rows.length > shown" variant="text" @click="shown += 20"
      >显示更多记录</t-button
    >
    <p class="quote-note">
      最新报价按官方列表约60秒核验；走势约30秒核验。官方没有新报价时保持原值；仅展示实际取得的记录。
    </p>
  </section>
</template>
<style scoped>
.official-odds {
  padding: 0 24px 24px;
  color: #25384d;
  min-width: 0;
  overflow-wrap: anywhere;
}
.quote-toolbar,
.quote-controls,
.quote-tabs,
.quote-status,
.quote-legend,
.quote-inspect {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.quote-toolbar {
  justify-content: space-between;
}
.quote-tabs button,
.quote-controls button,
.quote-legend button,
.quote-controls select {
  background: #fff;
  border: 1px solid #dce5ec;
  padding: 8px 12px;
  color: #435a70;
  cursor: pointer;
  font: inherit;
}
.quote-tabs button[aria-pressed="true"],
.quote-controls button[aria-pressed="true"] {
  border-color: #315d80;
  background: #eaf1f6;
  color: #294e6c;
  font-weight: 600;
}
.quote-status {
  margin: 18px 0;
  font-size: 12px;
  color: #65758a;
}
.quote-warning {
  color: #a5682b;
}
.quote-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(125px, 1fr));
  gap: 12px;
}
.quote-option {
  display: grid;
  gap: 8px;
  border: 1px solid #e1e8ee;
  padding: 14px;
  background: #f8fafc;
}
.quote-option strong {
  font-size: 26px;
  font-variant-numeric: tabular-nums;
}
.quote-option > span {
  font-size: 13px;
}
.quote-option small {
  color: #65758a;
}
.up {
  color: #bd3656;
}
.down {
  color: #137c5e;
}
.flat {
  color: #65758a;
}
.quote-note {
  font-size: 12px;
  color: #65758a;
  line-height: 1.8;
}
.chart-title {
  margin-top: 28px;
}
.chart-title h2 {
  font-size: 19px;
}
.quote-legend {
  margin: 16px 0;
  gap: 8px;
}
.quote-legend button {
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.quote-legend button[aria-pressed="true"] {
  border-color: #7a8fa3;
}
.quote-legend i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.quote-chart svg {
  display: block;
  width: 100%;
  min-height: 180px;
}
.quote-chart text {
  fill: #65758a;
  font-size: 11px;
}
.quote-chart circle {
  cursor: pointer;
}
.quote-chart circle:focus {
  stroke: #25384d;
  stroke-width: 3;
  outline: none;
}
.quote-history article {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 16px;
  padding: 16px 0;
  border-top: 1px solid #e3e9ef;
}
.quote-history time {
  font-size: 13px;
  color: #65758a;
}
.history-values {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
  gap: 12px;
}
.history-values > div {
  display: grid;
  gap: 5px;
}
.history-values span,
.history-values small {
  font-size: 12px;
}
.history-values strong {
  font-size: 19px;
  font-variant-numeric: tabular-nums;
}
.active-row,
.quote-inspect {
  background: #edf4f8;
}
.quote-inspect {
  padding: 12px;
  font-size: 13px;
}
.official-odds h3 {
  font-size: 16px;
}
.official-odds h3 small {
  font-size: 12px;
  color: #65758a;
  font-weight: 400;
}
.quote-empty {
  padding: 32px;
  text-align: center;
  color: #65758a;
}
button:focus-visible,
select:focus-visible {
  outline: 2px solid #315d80;
  outline-offset: 2px;
}
@media (max-width: 600px) {
  .official-odds {
    padding: 0 14px 16px;
  }
  .quote-options {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }
  .quote-option {
    padding: 10px;
  }
  .quote-option strong {
    font-size: 22px;
  }
  .quote-history article {
    grid-template-columns: 1fr;
  }
  .quote-controls {
    gap: 8px;
  }
  .quote-tabs {
    gap: 6px;
  }
  .quote-tabs button {
    padding: 8px;
    font-size: 12px;
  }
}
.quote-inspect {
  display: block;
  margin: 16px 0 26px;
  padding: 18px 20px;
  background: #f1f6fa;
  border: 1px solid #d7e3ec;
  border-left: 3px solid #426c8d;
}
.inspect-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.inspect-heading strong {
  font-size: 14px;
}
.inspect-heading p {
  margin: 8px 0;
  font-size: 15px;
  font-variant-numeric: tabular-nums;
}
.inspect-heading small {
  color: #65758a;
}
.inspect-values {
  display: grid;
  grid-template-columns: repeat(var(--quote-columns), minmax(0, 1fr));
  gap: 16px;
}
.inspect-values > div {
  display: grid;
  gap: 10px;
  min-width: 0;
}
.inspect-values > div > span:first-child {
  display: flex;
  align-items: center;
  gap: 7px;
}
.inspect-values i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: none;
}
.inspect-values small {
  display: block;
  margin-top: 4px;
  font-size: 12px;
}
.price-transition {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  font-variant-numeric: tabular-nums;
}
.price-transition > span {
  font-size: 16px;
  color: #65758a;
}
.price-transition strong {
  font-size: 24px;
  color: #25384d;
}
.price-transition .transition-arrow {
  font-size: 14px;
  color: #8193a4;
}
.latest-record {
  border: 1px solid #c5d4e0;
  background: #fff;
  color: #315d80;
  padding: 6px 10px;
  font: inherit;
  cursor: pointer;
  flex: none;
}
.history-header,
.quote-history article {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 20px;
  padding: 16px;
}
.history-header {
  background: #f4f7fa;
  color: #65758a;
  font-size: 12px;
}
.history-header small {
  display: block;
  margin-top: 6px;
  font-weight: 400;
}
.history-values {
  grid-template-columns: repeat(var(--quote-columns), minmax(0, 1fr));
  gap: 12px;
}
.history-time {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
  min-width: 0;
}
.history-time button {
  border: 0;
  background: transparent;
  padding: 0;
  color: #315d80;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.history-time time {
  color: inherit;
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.history-time small,
.previous-price {
  font-size: 12px;
  color: #65758a;
}
.history-values .mobile-option-label {
  display: none;
}
.history-values strong {
  font-size: 22px;
}
.quote-history .active-row {
  background: #f1f6fa;
  box-shadow: inset 3px 0 #426c8d;
}
@media (max-width: 900px) {
  .history-header,
  .quote-history article {
    grid-template-columns: 190px minmax(0, 1fr);
    gap: 12px;
    padding: 14px 10px;
  }
  .inspect-values {
    gap: 12px;
  }
  .price-transition strong {
    font-size: 21px;
  }
}
@media (max-width: 600px) {
  .quote-inspect {
    padding: 14px 12px;
  }
  .inspect-heading p {
    font-size: 13px;
    line-height: 1.8;
  }
  .inspect-values {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 18px 12px;
  }
  .inspect-values > div {
    gap: 6px;
  }
  .inspect-heading {
    flex-wrap: wrap;
  }
  .quote-history .history-header {
    display: none;
  }
  .quote-history article {
    grid-template-columns: 1fr;
    gap: 18px;
    padding: 16px 12px;
  }
  .history-values {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    row-gap: 20px;
  }
  .history-values .mobile-option-label {
    display: block;
    color: #65758a;
  }
  .history-values strong {
    font-size: 20px;
  }
  .quote-chart .middle-tick {
    display: none;
  }
}
</style>

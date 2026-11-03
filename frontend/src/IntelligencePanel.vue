<script setup>
import { timeLabel } from "./api";
defineProps({
  data: Object,
  title: { type: String, default: "分析线索与核查建议" },
});
const columns = [
  { colKey: "label", title: "方向", width: 80 },
  { colKey: "first", title: "最早报价", width: 90 },
  { colKey: "last", title: "末次报价", width: 90 },
  { colKey: "range", title: "区间", width: 115 },
  { colKey: "delta", title: "参考概率变化", width: 130 },
  { colKey: "signal", title: "调价过程", width: 105 },
];
</script>

<template>
  <section v-if="data" class="panel mt16 intelligence-panel">
    <div class="panel-heading">
      <h2>{{ title }}</h2>
      <t-tag variant="light">{{
        data.phase === "pregame" ? "赛前核查" : "赛后回看"
      }}</t-tag>
    </div>
    <ul class="risk-list">
      <li v-for="item in data.lineups" :key="item.side">{{ item.summary }}</li>
    </ul>
    <div v-for="market in data.markets" :key="market.code" class="mt16">
      <div class="panel-heading">
        <h3>{{ market.label }} · {{ market.count }} 条报价</h3>
        <span v-if="market.giving_team" class="small muted"
          >{{ market.giving_team }}让球 · 主队让球数
          {{ market.line > 0 ? "+" : "" }}{{ market.line }}</span
        >
      </div>
      <t-table
        row-key="key"
        :data="market.rows"
        :columns="columns"
        size="small"
      >
        <template #first="{ row }">{{ row.first.toFixed(2) }}</template>
        <template #last="{ row }">{{ row.last.toFixed(2) }}</template>
        <template #range="{ row }">{{
          row.low == null
            ? "让球数有变"
            : `${row.low.toFixed(2)}–${row.high.toFixed(2)}`
        }}</template>
        <template #delta="{ row }">{{
          row.delta_pp == null
            ? "不可直接比较"
            : `${row.delta_pp > 0 ? "+" : ""}${row.delta_pp.toFixed(2)} 个百分点`
        }}</template>
        <template #signal="{ row }">{{
          !market.same_line
            ? "让球数有变"
            : row.reversal
              ? "曾反向调价"
              : market.count === 1
                ? "仅一条记录"
                : row.first === row.last
                  ? "无净变化"
                  : "单向变化"
        }}</template>
      </t-table>
      <p class="section-note">
        {{ timeLabel(market.first_at) }} → {{ timeLabel(market.last_at) }} ·
        来源：体彩 SP
      </p>
    </div>
    <div v-for="item in data.evidence" :key="item.id" class="source-evidence">
      <t-tag variant="light"
        >{{ item.label }} ·
        {{ item.pregame_eligible ? "赛前录入" : "赛后补充" }}</t-tag
      >
      <p>{{ item.text }}</p>
      <a :href="item.source_url" target="_blank" rel="noopener noreferrer"
        >查看来源</a
      >
    </div>
    <h3 class="section-note">下一步核查</h3>
    <ul class="risk-list">
      <li v-for="item in data.checks" :key="item">{{ item }}</li>
    </ul>
    <p v-for="item in data.limitations" :key="item" class="section-note">
      {{ item }}
    </p>
  </section>
</template>

<style scoped>
.intelligence-panel :deep(.t-table td),
.intelligence-panel :deep(.t-table th) {
  white-space: nowrap;
}
.intelligence-panel h3 {
  font-size: 14px;
}
</style>

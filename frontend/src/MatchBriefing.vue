<script setup>
import { timeLabel } from './api';
import MarketForecast from './MarketForecast.vue';
defineProps({ data: Object, matchId: String });
const labels = { available: '已取得', partial: '部分资料', missing: '待补齐', pending: '待公布', stale: '待更新' };
const columns = [
  { colKey: 'title', title: '分析项目', width: 125 },
  { colKey: 'status', title: '状态', width: 100 },
  { colKey: 'summary', title: '实际覆盖', minWidth: 330, ellipsis: true },
  { colKey: 'source', title: '来源', width: 155, ellipsis: true },
  { colKey: 'captured_at', title: '资料时间', width: 170 },
  { colKey: 'tab', title: '查看', width: 70 },
];
</script>
<template>
  <section v-if="data" class="panel match-briefing mb16" aria-label="自动比赛研判">
    <div class="panel-heading"><h2>{{ data.title }}</h2><t-tag variant="light">自动更新 · 未归档</t-tag></div>
    <p class="section-note">汇总于 {{ timeLabel(data.created_at) }} · 当前资料随采集更新，已保存预测版本独立保留。</p>
    <div v-if="data.preview" class="brief-probabilities">
      <div><span class="small muted">体彩市场参考</span><h3>{{ data.preview.title }}</h3></div>
      <div v-for="(p, i) in data.preview.probabilities" :key="i"><span>{{ ['主胜','平局','客胜'][i] }}</span><strong>{{ p.toFixed(1) }}%</strong></div>
    </div>
    <p v-else-if="data.phase === 'pregame'" class="section-note">当前报价条件不足，暂不展示即时方向概率；已有事实分析如下。</p>
    <div class="brief-findings"><article v-for="item in data.findings" :key="item.title"><h3>{{ item.title }}</h3><p>{{ item.text }}</p><a :href="`#/match/${matchId}/${item.tab}`">核对依据</a></article></div>
    <details class="brief-details" open>
      <summary>资料覆盖：{{ data.available_count }}/{{ data.coverage_count }} 项已取得 · 展开核查来源</summary>
      <t-table row-key="key" :data="data.coverage" :columns="columns" size="small">
        <template #status="{ row }"><t-tag :theme="row.status === 'available' ? 'success' : 'warning'" variant="light">{{ labels[row.status] }}</t-tag></template>
        <template #captured_at="{ row }">{{ row.captured_at ? timeLabel(row.captured_at) : '—' }}</template>
        <template #tab="{ row }"><a :href="`#/match/${matchId}/${row.tab}`">查看</a></template>
      </t-table>
    </details>
    <details v-if="data.preview?.market_assessments?.length" class="brief-details"><summary>当前五种玩法参考 · 与已归档预测分开</summary><MarketForecast :rows="data.preview.market_assessments" /></details>
    <details v-if="data.football_model?.status === 'shadow'" class="brief-details"><summary>本赛事足球模型 · 前瞻观察</summary><MarketForecast :rows="data.football_model.markets" /></details>
    <h3 class="section-note">判断条件与下一步</h3>
    <ul class="risk-list"><li v-for="item in data.actions" :key="item">{{ item }}</li></ul>
    <p v-if="data.phase !== 'pregame'" class="section-note"><a :href="data.review_url">查看关联复盘与赛前归档</a></p>
  </section>
</template>
<style scoped>
.brief-probabilities { display:grid; grid-template-columns:minmax(210px,1fr) repeat(3,90px); gap:20px; padding:20px; border-block:1px solid #e7e9ed; align-items:center; }
.brief-probabilities strong { display:block; font-size:24px; margin-top:6px; }
.brief-probabilities h3 { margin:8px 0 0; }
.brief-findings { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); padding:20px; gap:20px; }
.brief-findings h3 { font-size:14px; margin:0 0 8px; }
.brief-findings p { margin:0 0 8px; line-height:1.8; }
.brief-details { margin:0 20px 18px; min-width:0; }
.brief-details summary { cursor:pointer; padding:12px 0; font-weight:600; }
.match-briefing :deep(td),.match-briefing :deep(th) { white-space:nowrap; }
@media(max-width:700px) { .brief-findings { grid-template-columns:1fr; } .brief-probabilities { grid-template-columns:repeat(3,1fr); } .brief-probabilities>div:first-child { grid-column:1/-1; } }
</style>

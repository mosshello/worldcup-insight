<script setup>
import { ref, computed } from 'vue';
import { beijingDateTime } from './api';
import { markets, nodes, sources, percent } from './observationView';
const props=defineProps({data:Object,competition:String,node:String,market:String});
const open=ref(false);
const leagues={'eng.premier':'英超','esp.laliga':'西甲','ger.bundesliga':'德甲','ita.serie_a':'意甲','fra.ligue1':'法甲'};
const evaluation=computed(()=>props.data?.research_status?.evaluation);
const cells=computed(()=>(evaluation.value?.cells||[]).filter(r=>(!props.competition||r.competition_id===props.competition)&&(!props.node||r.node===props.node)&&(!props.market||r.market===props.market)));
const marketResearch=computed(()=>(props.data?.source_status||[]).find(r=>r.id==='market-research'));
const reports=computed(()=>(marketResearch.value?.reports||[]).filter(r=>!props.competition||r.competition_id===props.competition));
const state=cell=>!cell.n?'观察中':({observing:'观察中',eligible_for_manual_review:'待人工复核',not_promoted:'未通过选用条件',awaiting_family_check:'待联合检验',invalid_family:'试验分组待核查'}[cell.status]||'待核查');
</script>
<template>
  <t-button variant="text" @click="open=true">研究状态</t-button>
  <t-drawer v-model:visible="open" header="研究状态" :footer="false" size="min(780px, 100vw)">
    <div class="research-status"><h2>前瞻评估</h2><p v-if="!cells.length">观察中 · 暂无符合条件的已结算样本</p><p v-if="evaluation?.evaluated_at" class="muted">{{beijingDateTime(evaluation.evaluated_at)}}</p>
      <section v-for="cell in cells" :key="[cell.experiment_id,cell.node,cell.source,cell.market].join(':')"><h3>{{leagues[cell.competition_id]||cell.competition_id}} · {{nodes[cell.node]}} · {{sources[cell.source]}} · {{markets[cell.market]}}</h3><strong>{{state(cell)}}</strong><dl><dt>共同样本</dt><dd>{{cell.n ?? 0}} 场</dd><dt>目标比赛</dt><dd>{{cell.targets ?? 0}} 场</dd><dt>覆盖率</dt><dd>{{percent(cell.coverage)}}</dd></dl><p v-for="reason in cell.reasons||[]" :key="reason">{{reason}}</p><details><summary>检验记录</summary><pre>{{JSON.stringify(cell,null,2)}}</pre></details></section>
      <h2>盘口价格研究</h2><p v-if="marketResearch?.error">{{marketResearch.error}}</p><p v-if="!reports.length" class="muted">观察中 · 暂无价格研究报告</p><section v-for="report in reports" :key="report.artifact_id||report.competition_id"><h3>{{leagues[report.competition_id]||report.competition_id}}</h3><p>{{report.monitoring_validated?'价格持续性已验证':'价格持续性观察中'}}</p><dl><dt>价格事件</dt><dd>{{report.events ?? 0}}</dd><dt>覆盖时长</dt><dd>{{report.covered_fixture_seconds ?? 0}} 比赛秒</dd><dt>生产启用</dt><dd>{{report.production_enabled?'已启用':'未启用'}}</dd></dl><details><summary>阈值与验证</summary><pre>{{JSON.stringify({threshold:report.threshold,train:report.train,validation:report.validation},null,2)}}</pre></details></section>
    </div>
  </t-drawer>
</template>
<style scoped>
.research-status section{border-top:1px solid #e7e9ed;padding:18px 0}.research-status h2{font-size:20px}.research-status h3{font-size:15px;overflow-wrap:anywhere}.research-status dl{display:grid;grid-template-columns:95px minmax(0,1fr);gap:10px}.research-status dd{margin:0;overflow-wrap:anywhere}.research-status summary{cursor:pointer;color:#0052d9}.research-status pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f8;padding:12px;font:12px/1.7 monospace}.research-status p{overflow-wrap:anywhere}
</style>

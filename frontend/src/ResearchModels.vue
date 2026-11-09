<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import { api, beijingDateTime } from './api';
import { researchLabel, researchState, researchCoverage, researchVariants } from './researchView';
import { markets, percent } from './observationView';
const props=defineProps({competition:String});
const data=ref(null),loading=ref(false),error=ref(''),selected=ref(null);let sequence=0;
const supported=computed(()=>['eng.premier','esp.laliga','ger.bundesliga','ita.serie_a','fra.ligue1'].includes(props.competition));
const rows=computed(()=>{const league=data.value?.competitions?.find(x=>x.competition_id===props.competition);return league?[...['base','enhanced','personnel'].filter(k=>league[k]).map(k=>({kind:k,...league[k],alerts:[...(league.alerts||[]),...(league[k].alerts||[])]})),...(league.research_candidates||[])]:[];});
const names={base:'基础对照',enhanced:'xG与休息增强',personnel:'历史人员与教练',catboost:'CatBoost胜平负',catboost_had:'CatBoost胜平负',lightgbm_goals:'LightGBM进球'};
const reportGroups={...researchVariants,linear_personnel:'人员特征线性基准',catboost:'CatBoost胜平负',catboost_had:'CatBoost胜平负',lightgbm_goals:'LightGBM进球',baseline:'基础模型',enhanced:'增强候选',personnel:'人员候选'};
const fields=row=>(row.input_metadata?.actually_used_fields||[]).map(researchLabel).join(' / ')||'未记录';
async function load(){const current=++sequence;data.value=null;error.value='';selected.value=null;if(!supported.value)return;loading.value=true;try{const result=await api.observations({competition:props.competition});if(current===sequence)data.value=result.research_status;}catch(e){if(current===sequence)error.value='研究模型读取失败';}finally{if(current===sequence)loading.value=false;}}
watch(()=>props.competition,load,{immediate:true});onBeforeUnmount(()=>sequence++);
</script>
<template>
  <section v-if="supported" class="panel research-models" aria-label="研究模型">
    <div class="panel-heading"><h2>研究模型</h2><t-button variant="text" :loading="loading" @click="load">刷新研究状态</t-button></div>
    <t-alert v-if="error" theme="warning">{{error}}<t-button variant="text" @click="load">重试</t-button></t-alert>
    <p v-if="loading&&!data" class="research-empty">加载中</p><p v-else-if="!rows.length&&!error" class="research-empty">暂无研究模型</p>
    <article v-for="row in rows" :key="row.kind" class="research-row"><div><strong>{{names[row.kind]}}</strong><small>{{researchState(row)}}</small></div><div><span>实际输入</span><p>{{fields(row)}}</p></div><div><span>训练样本</span><p>{{researchCoverage(row)}}</p></div><t-button variant="text" @click="selected=row">输入与报告</t-button></article>
  </section>
  <t-drawer :visible="!!selected" header="研究模型详情" :footer="false" size="min(760px, 100vw)" @update:visible="v=>{if(!v)selected=null}">
    <div v-if="selected" class="research-detail"><h2>{{names[selected.kind]}}</h2><dl><dt>选用状态</dt><dd>{{researchState(selected)}}</dd><dt>模型版本</dt><dd>{{selected.model_id||selected.run_id||selected.digest||'未记录'}}</dd><dt>实际输入</dt><dd>{{fields(selected)}}</dd><template v-if="selected.supported_markets?.length"><dt>支持玩法</dt><dd>{{selected.supported_markets.map(m=>markets[m]||m).join(' / ')}}</dd></template><dt>未入模</dt><dd>{{(selected.input_metadata?.not_trained||[]).map(researchLabel).join(' / ')||'未记录'}}</dd><dt>训练时间</dt><dd>{{selected.trained_before?beijingDateTime(selected.trained_before):'未记录'}}</dd></dl>
      <details open><summary>数据覆盖</summary><pre>{{JSON.stringify(selected.coverage||{},null,2)}}</pre></details>
      <section v-for="(report,index) in selected.reports||[]" :key="index" class="research-report"><h3>{{report.name}}</h3><p>{{report.current_artifact_evaluation===false?'历史候选对照':report.status}} · {{report.sample_size ?? '—'}} 场</p><p v-if="report.evaluated_model_id">受测版本 {{report.evaluated_model_id}}</p><p v-if="report.current_artifact_evaluation===false" class="muted">当前版本尚无同版评估</p><div v-for="(metric,market) in report.metrics||{}" :key="market" class="research-metric"><strong>{{markets[market]||market}}</strong><div v-for="(values,group) in metric" :key="group" v-show="values &amp;&amp; typeof values=== 'object'"><span>{{reportGroups[group]||group}}</span><span v-if="['catboost','catboost_had'].includes(group)&&market!=='had'">不支持</span><template v-else><span>{{values?.n ?? '—'}} 场</span><span>单选 {{percent(values?.accuracy)}}</span><span>Log loss {{values?.log_loss?.toFixed(3) ?? '—'}}</span><span>Brier {{values?.brier?.toFixed(3) ?? '—'}}</span></template></div></div><p>{{report.source}}</p></section><p v-if="!selected.reports?.length" class="muted">暂无评估报告</p>
      <details v-if="selected.variants?.length"><summary>候选版本</summary><section v-for="variant in selected.variants" :key="variant.model_id"><h3>{{researchVariants[variant.variant]||variant.variant}}</h3><p>{{variant.train_count ?? '—'}} 场</p><p>{{variant.model_id}}</p><p>{{(variant.fields||[]).map(researchLabel).join(' / ')}}</p></section></details><details v-if="selected.alerts?.length"><summary>变化核查</summary><p v-for="(alert,index) in selected.alerts" :key="index">{{alert.message||researchLabel(alert.kind)}}</p></details>
    </div>
  </t-drawer>
</template>
<style scoped>
.research-metric{border-top:1px solid #e7e9ed;padding:14px 0}.research-metric>div{display:flex;flex-wrap:wrap;gap:8px 16px;padding-top:8px}.research-empty{padding:20px 24px;color:#536579}.research-row{display:grid;grid-template-columns:170px minmax(0,1fr) 130px 110px;gap:20px;padding:20px 24px;border-top:1px solid #e7e9ed;align-items:center}.research-row small{display:block;color:#536579;margin-top:8px}.research-row span{color:#536579;font-size:12px}.research-row p{margin:6px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.research-detail dl{display:grid;grid-template-columns:90px minmax(0,1fr);gap:14px}.research-detail dd{margin:0;overflow-wrap:anywhere}.research-detail pre{font:12px/1.7 monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f8;padding:12px}.research-detail details,.research-report{padding:16px 0;border-top:1px solid #e7e9ed}.research-detail summary{cursor:pointer;color:#0052d9}.research-report p{overflow-wrap:anywhere}@media(max-width:700px){.research-row{grid-template-columns:1fr 1fr;gap:12px;padding:16px}.research-row>div:nth-child(2){grid-column:1/-1;grid-row:2}.research-row .t-button{justify-self:end;min-height:44px}.research-row>div:nth-child(3){grid-row:1;grid-column:2}}
</style>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { api, beijingDateTime } from './api';
import { csvCell } from './domain';
import { aiUsage, aiCostEstimate, researchLabel } from './researchView';
import ObservationEvidence from './ObservationEvidence.vue';
import ObservationResearchStatus from './ObservationResearchStatus.vue';
import { markets, nodes, sources, statuses, probabilities, optionLabel, percent, observationText, observationExperiments, recordExperimentId } from './observationView';
const initial=new URLSearchParams(location.hash.split('?')[1]||'');
const competition=ref(initial.get('competition')||''), day=ref(''), source=ref('base'), market=ref('had'), node=ref('t60'), query=ref('');
const fixtureFilter=ref(initial.get('fixture_id')||'');
const data=ref(null), loading=ref(false), error=ref(''), detail=ref(null), detailOpen=ref(false), detailError=ref(''), detailLoading=ref(false);
const page=ref(1), detailNode=ref('t60'), detailSource=ref('base'), detailExperiment=ref('');
function teamName(row,side){return ['球队译名待补充','未知球队'].includes(row?.[side]) ? (row?.[side+'_name']||row[side]) : row?.[side];}
function handicapLabel(record){const n=record.result?.handicap??record.result?.line??record.line;return Number.isFinite(n)?` (${n>0?'+':''}${n})`:'';}
const leagues={'eng.premier':'英超','esp.laliga':'西甲','ger.bundesliga':'德甲','ita.serie_a':'意甲','fra.ligue1':'法甲'};
let sequence=0, detailSequence=0, disposed=false, timer;
async function load(){const seq=++sequence;loading.value=true;error.value='';try{const result=await api.observations({competition:competition.value,day:day.value,source:source.value,market:market.value,node:node.value,fixture_id:fixtureFilter.value});if(!disposed&&seq===sequence)data.value=result;}catch(e){if(seq===sequence)error.value=e.message;}finally{if(seq===sequence)loading.value=false;}}
const rows=computed(()=>(data.value?.rows||[]).filter(r=>`${teamName(r,'home')} ${teamName(r,'away')}`.includes(query.value.trim())));
const shown=computed(()=>rows.value.slice((page.value-1)*15,page.value*15));
const summary=computed(()=>data.value?.summary||{});
const aiBudget=computed(()=>(data.value?.source_status||[]).find(s=>s.id==='deepseek_observation'));
const aiNotice=computed(()=>['ai','enhanced'].includes(source.value)&&aiBudget.value?.status==='blocked'?({not_configured:'赛前AI尚未配置',daily_call_budget:'赛前AI今日调用额度已用完',daily_token_budget:'赛前AI今日Token额度不足'}[aiBudget.value.reason]||'赛前AI暂不可用'):'');
const detailExperiments=computed(()=>observationExperiments(detail.value));
const selectedRecords=computed(()=>(detail.value?.observations||[]).filter(r=>(r.node===detailNode.value)&&(r.source===detailSource.value)&&recordExperimentId(r)===detailExperiment.value));
let selectedId='';
async function openDetail(row){selectedId=row.id;detailOpen.value=true;detail.value=null;detailExperiment.value='';detailError.value='';detailLoading.value=true;const seq=++detailSequence;try{const result=await api.observationDetail(row.id);if(!disposed&&seq===detailSequence){detail.value=result;detailExperiment.value=observationExperiments(result).find(e=>e.active)?.id||'';}}catch(e){if(seq===detailSequence)detailError.value=e.message;}finally{if(seq===detailSequence)detailLoading.value=false;}}
function exportRows(){const values=[['赛事','主队','客队','北京时间','来源','玩法',...Object.values(nodes)],...rows.value.map(r=>[leagues[r.competition_id]||r.competition_id,teamName(r,'home'),teamName(r,'away'),beijingDateTime(r.kickoff),sources[source.value],markets[market.value],...Object.keys(nodes).map(n=>observationText(r.observations?.[n],market.value))])];const csv=values.map(row=>row.map(csvCell).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='五大联赛观察.csv';a.click();URL.revokeObjectURL(url);}
watch([competition,day,source,market,node,fixtureFilter],()=>{page.value=1;load();});watch(query,()=>page.value=1);
function syncHash(){const params=new URLSearchParams(location.hash.split('?')[1]||'');detailOpen.value=false;detail.value=null;detailSequence++;competition.value=params.get('competition')||'';fixtureFilter.value=params.get('fixture_id')||'';}
onMounted(()=>{window.addEventListener('hashchange',syncHash);load();timer=setInterval(()=>{if(!document.hidden&&!loading.value)load();},30000);});onBeforeUnmount(()=>{window.removeEventListener('hashchange',syncHash);disposed=true;sequence++;detailSequence++;clearInterval(timer);});
</script>
<template>
  <section class="observation-workspace">
    <div class="page-heading"><h1>五大联赛观察</h1><div class="obs-actions"><ObservationResearchStatus :data="data" :competition="competition" :node="node" :market="market" /><t-button variant="outline" :loading="loading" @click="load">刷新</t-button></div></div>
    <div class="panel obs-filters">
      <label>赛事<select v-model="competition" aria-label="观察赛事"><option value="">全部五大联赛</option><option v-for="(label,id) in leagues" :key="id" :value="id">{{label}}</option></select></label>
      <label>比赛日期<input v-model="day" type="date" aria-label="观察日期" /></label>
      <label>预测来源<select v-model="source" aria-label="预测来源"><option v-for="(label,id) in sources" :key="id" :value="id">{{label}}</option></select></label>
      <label>玩法<select v-model="market" aria-label="观察玩法"><option v-for="(label,id) in markets" :key="id" :value="id">{{label}}</option></select></label>
      <label>统计时点<select v-model="node" aria-label="统计时点"><option v-for="(label,id) in nodes" :key="id" :value="id">{{label}}</option></select></label>
      <label>球队<input v-model="query" aria-label="搜索球队" placeholder="搜索球队" /></label>
      <t-button v-if="fixtureFilter" variant="text" @click="fixtureFilter=''">查看全部比赛</t-button>
    </div>
    <t-alert v-if="error" theme="warning" class="mb16"><span>{{ error }}</span><t-button variant="text" @click="load">重试</t-button></t-alert>
    <div v-if="!data&&loading" class="panel obs-empty" role="status">加载中</div>
    <template v-if="data">
      <t-alert v-if="aiNotice" theme="warning" class="mb16">{{aiNotice}}</t-alert>
      <details v-if="aiBudget&&['ai','enhanced'].includes(source)" class="panel obs-evaluation"><summary>AI运行状态</summary><p>调用 {{aiBudget.calls_used ?? '—'}} / {{aiBudget.calls_limit ?? '—'}} · 成功 {{aiBudget.completed ?? 0}} · 失败 {{aiBudget.failed ?? 0}}</p><p>Token {{aiBudget.tokens_accounted ?? '—'}} / {{aiBudget.tokens_limit ?? '—'}}</p><p>费用 {{aiBudget.cost ?? '未提供'}} · 配额日期 {{aiBudget.day_utc}}（UTC）</p></details>
      <div class="panel obs-summary" aria-label="当前时点统计">
        <div><span>目标比赛</span><strong>{{summary.targets ?? rows.length}}</strong></div>
        <div><span>单选准确率</span><strong>{{percent(summary.single_accuracy)}}</strong><small>{{summary.single_correct ?? 0}} / {{summary.single_scored ?? summary.scored ?? 0}}</small></div>
        <div><span>多选覆盖率</span><strong>{{percent(summary.multi_coverage)}}</strong><small>{{summary.multi_covered ?? 0}} / {{summary.multi_scored ?? 0}}</small></div>
        <div><span>未预测</span><strong>{{summary.unpredicted ?? ((summary.unavailable||0)+(summary.missed||0)+(summary.failed||0)+(summary.pending||0)+(summary.unsupported||0))}}</strong></div>
      </div>
      <details class="panel obs-evaluation"><summary>评估详情 · {{nodes[node]}}</summary><div class="obs-eval-grid"><span>概率误差（Log loss） <b>{{summary.log_loss?.toFixed(3) ?? '—'}}</b></span><span>Brier <b>{{summary.brier?.toFixed(3) ?? '—'}}</b></span><span>失败 {{summary.failed||0}}</span><span>错过时点 {{summary.missed||0}}</span><span>晚到记录 {{summary.late||0}}</span><span>不支持 {{summary.unsupported||0}}</span></div><p v-if="summary.single_accuracy_interval">单选区间 {{percent(summary.single_accuracy_interval[0])}} ～ {{percent(summary.single_accuracy_interval[1])}}</p><details v-if="summary.calibration?.length"><summary>概率校准</summary><p v-for="bin in summary.calibration" :key="bin.lower">预测 {{percent(bin.mean_probability)}} · 实际 {{percent(bin.frequency)}} · {{bin.count}}场</p></details><div v-if="data.comparison"><p>四组共同比赛 {{data.comparison.common_n||0}} 场</p><p v-for="(group,key) in data.comparison.groups" :key="key">{{sources[key]||key}} · {{group.n??0}} 场 · Log loss {{group.log_loss?.toFixed(3)??'—'}} · Brier {{group.brier?.toFixed(3)??'—'}}</p></div></details>
      <div class="panel obs-list"><div class="panel-heading"><h2>比赛观察 <small>{{rows.length}}</small></h2><t-button variant="outline" :disabled="!rows.length" @click="exportRows">导出列表</t-button></div>
        <div class="obs-row obs-head"><span>比赛 / 北京时间</span><span v-for="label in nodes" :key="label">{{label}}</span><span>操作</span></div>
        <article v-for="row in shown" :key="row.id" class="obs-row">
          <div class="obs-fixture"><small>{{leagues[row.competition_id]||row.competition_id}}</small><strong>{{teamName(row,'home')}} <span class="muted">vs</span> {{teamName(row,'away')}}</strong><time>{{beijingDateTime(row.kickoff)}}</time></div>
          <div v-for="(label,key) in nodes" :key="key" class="obs-cell"><small class="obs-mobile-label">{{label}}</small><button class="obs-prediction" @click="openDetail(row);detailNode=key;detailSource=source">{{observationText(row.observations?.[key],market)}}</button><small v-if="row.observations?.[key]?.label">{{row.observations[key].label}}</small><small v-if="row.observations?.[key]?.strict_node === false">非准点记录</small><small v-if="row.observations?.[key]?.actual">赛果：{{optionLabel(market,row.observations[key].actual)}}</small></div>
          <t-button variant="text" @click="openDetail(row);detailNode=node;detailSource=source">详情</t-button>
        </article>
        <div v-if="!rows.length" class="obs-empty">暂无符合条件的比赛</div>
        <div v-if="rows.length>15" class="obs-pagination"><t-button variant="outline" :disabled="page===1" @click="page--">上一页</t-button><span>{{page}} / {{Math.ceil(rows.length/15)}}</span><t-button variant="outline" :disabled="page*15>=rows.length" @click="page++">下一页</t-button></div>
      </div>
    </template>
    <t-drawer v-model:visible="detailOpen" header="观察详情" :footer="false" size="min(900px, 100vw)" :destroy-on-close="true">
      <div v-if="detailLoading" role="status">加载中</div><t-alert v-if="detailError" theme="error">{{detailError}}<t-button variant="text" @click="openDetail({id:selectedId})">重试</t-button></t-alert>
      <div v-if="detail" class="obs-detail">
        <h2>{{detail.fixture?.home}} vs {{detail.fixture?.away}}</h2><p>{{beijingDateTime(detail.fixture?.kickoff)}}</p>
        <div class="obs-detail-filters"><label v-if="detailExperiments.length">实验版本<select v-model="detailExperiment" aria-label="实验版本"><option v-for="experiment in detailExperiments" :key="experiment.id" :value="experiment.id">{{experiment.created_at?'登记 '+beijingDateTime(experiment.created_at):'登记时间未记录'}} · {{experiment.active?'当前':'历史'}}</option></select></label><label>观察时点<select v-model="detailNode" aria-label="详情观察时点"><option v-for="(label,id) in nodes" :key="id" :value="id">{{label}}</option></select></label><label>来源<select v-model="detailSource" aria-label="详情预测来源"><option v-for="(label,id) in sources" :key="id" :value="id">{{label}}</option></select></label></div>
        <p v-if="!selectedRecords.length">该时点尚无记录</p>
        <section v-for="record in selectedRecords" :key="record.id" class="obs-record">
          <p>{{statuses[record.status]||record.status}} · {{beijingDateTime(record.result?.created_at || record.created_at)}} <span v-if="record.label">· {{record.label}}</span></p>
          <t-alert v-if="record.error||record.reason||record.result?.error||record.result?.reason" theme="warning">{{record.error||record.reason||record.result?.error||record.result?.reason}}</t-alert>
          <div v-for="(label,code) in markets" :key="code" class="obs-market"><strong>{{label}}{{code==='hhad'?handicapLabel(record):''}}</strong><span v-if="!probabilities(record,code).length">{{record.status==='completed'?'不支持':'暂无预测'}}</span><div v-else class="obs-probabilities"><span v-for="[key,p] in probabilities(record,code)" :key="key">{{optionLabel(code,key)}} <b>{{percent(p)}}</b></span></div></div>
          <details><summary>资料与版本</summary><dl><dt>实验ID</dt><dd>{{record.experiment_id||'未记录'}}</dd><dt>模型</dt><dd>{{record.model_version||record.result?.model_version||record.result?.model_id||record.result?.model||'未记录'}}</dd><dt>截止时间</dt><dd>{{beijingDateTime(record.cutoff_at||record.cutoff||record.input_cutoff||record.due_at)}}</dd><dt>运行耗时</dt><dd>{{aiUsage(record).duration ?? '—'}} ms</dd><dt>输入 / 输出Token</dt><dd>{{aiUsage(record).input ?? '—'}} / {{aiUsage(record).output ?? '—'}}</dd><dt>总Token</dt><dd>{{aiUsage(record).tokens ?? '—'}}</dd><dt>实际费用</dt><dd>{{aiUsage(record).cost ?? '未提供'}}</dd><template v-if="aiCostEstimate(record)"><dt>费用估算</dt><dd>{{aiCostEstimate(record).text}}<a v-if="aiCostEstimate(record).source_url" :href="aiCostEstimate(record).source_url" target="_blank" rel="noopener noreferrer"> 价目来源</a></dd></template><dt>响应模型</dt><dd>{{aiUsage(record).model||'未记录'}}</dd><template v-if="record.result?.input_metadata"><dt>实际输入</dt><dd>{{(record.result.input_metadata.actually_used_fields||[]).map(researchLabel).join(' / ')||'未记录'}}</dd><dt>未入模</dt><dd>{{(record.result.input_metadata.not_trained||[]).map(researchLabel).join(' / ')||'未记录'}}</dd></template></dl><pre>{{JSON.stringify(record.result?.evidence||record.evidence||record.result?.input_snapshot?.evidence||record.result?.ai_call?.input_snapshot?.evidence||record.input_snapshot?.evidence||[],null,2)}}</pre></details>
          <details v-if="record.result?.market_aware"><summary>AI带赔率对照 · 不计入独立组</summary><t-alert v-if="record.result.market_aware.error" theme="warning">{{record.result.market_aware.error}}</t-alert><div v-for="(label,code) in markets" :key="code" class="obs-market"><strong>{{label}}</strong><div class="obs-probabilities"><span v-for="[key,p] in probabilities(record.result.market_aware,code)" :key="key">{{optionLabel(code,key)}} <b>{{percent(p)}}</b></span><span v-if="!probabilities(record.result.market_aware,code).length">暂无预测</span></div></div></details>
        </section>
        <details><summary>盘口变化 · {{detail.events?.length||0}}</summary><div v-for="(event,index) in detail.events||[]" :key="index" class="obs-event"><time>{{beijingDateTime(event.created_at||event.captured_at||event.after?.captured_at)}}</time><pre>{{JSON.stringify(event,null,2)}}</pre></div><p v-if="!detail.events?.length">暂无变化记录</p></details>
        <details><summary>原始报价 · {{detail.quotes?.length||0}}</summary><pre>{{JSON.stringify(detail.quotes||[],null,2)}}</pre></details>
        <ObservationEvidence :summary="detail.evidence_summary" />
      </div>
    </t-drawer>
  </section>
</template>
<style scoped>
.obs-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.obs-evaluation{padding:16px 24px;margin-bottom:16px}.obs-evaluation summary{cursor:pointer;color:#0052d9;min-height:28px}.obs-eval-grid{display:flex;gap:16px;flex-wrap:wrap;margin-top:16px}.obs-filters{display:flex;flex-wrap:wrap;gap:16px;padding:20px;margin-bottom:16px}.obs-filters label,.obs-detail-filters label{display:grid;gap:8px;min-width:130px;flex:1;color:#536579}select,input{font:inherit;border:1px solid #dcdfe5;border-radius:0;min-height:40px;background:white;color:#1f2329;padding:8px;min-width:0;width:100%;box-sizing:border-box}select:focus-visible,input:focus-visible,button:focus-visible{outline:2px solid #0052d9;outline-offset:2px}.obs-summary{display:grid;grid-template-columns:repeat(4,1fr);padding:24px;margin-bottom:16px}.obs-summary>div{display:grid;gap:8px;border-right:1px solid #e7e9ed;padding:0 20px}.obs-summary>div:last-child{border:0}.obs-summary span,.obs-summary small,.obs-fixture small,.obs-fixture time,.obs-cell small{color:#536579}.obs-summary strong{font-size:26px}.obs-row{display:grid;grid-template-columns:minmax(210px,2fr) repeat(3,minmax(120px,1fr)) 64px;align-items:center;gap:16px;padding:18px 24px;border-top:1px solid #e7e9ed}.obs-head{background:#f5f6f8;color:#536579}.obs-fixture{min-width:0;display:grid;gap:8px}.obs-fixture strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.obs-cell{display:grid;gap:6px;min-width:0}.obs-prediction{border:0;background:none;text-align:left;color:#0052d9;padding:8px 0;font:inherit;cursor:pointer}.obs-mobile-label{display:none}.obs-empty{padding:60px 20px;text-align:center;color:#536579}.obs-pagination{display:flex;justify-content:flex-end;align-items:center;gap:20px;padding:20px}.obs-detail-filters{display:flex;gap:16px;margin:20px 0}.obs-record{border-top:1px solid #dcdfe5;padding:16px 0}.obs-market{display:grid;grid-template-columns:90px 1fr;gap:16px;padding:16px 0;border-bottom:1px solid #eee}.obs-probabilities{display:flex;flex-wrap:wrap;gap:12px 20px}.obs-probabilities span{white-space:nowrap}.obs-detail details{padding:16px 0;border-bottom:1px solid #e7e9ed}.obs-detail summary{cursor:pointer;color:#0052d9;min-height:32px}.obs-detail pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 monospace;background:#f5f6f8;padding:12px}.obs-detail dl{display:grid;grid-template-columns:90px 1fr;gap:10px}.obs-detail dd{margin:0;overflow-wrap:anywhere}.obs-event{border-top:1px solid #ddd;padding:12px 0}
@media(max-width:900px){.obs-row{grid-template-columns:minmax(160px,1.4fr) repeat(3,minmax(100px,1fr)) 48px;gap:8px;padding:16px}.obs-summary{padding:16px}.obs-summary>div{padding:0 10px}}
@media(max-width:700px){.obs-filters{gap:12px;padding:16px}.obs-filters label{flex-basis:calc(50% - 12px);min-width:0}select,input{min-height:44px}.obs-summary{grid-template-columns:repeat(2,1fr);gap:20px}.obs-summary>div:nth-child(2){border:0}.obs-head{display:none}.obs-row{grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.obs-fixture{grid-column:1/-1}.obs-fixture strong{white-space:normal}.obs-cell small{font-size:12px}.obs-mobile-label{display:block}.obs-row>.t-button{grid-column:1/-1;justify-self:end;min-height:44px}.obs-prediction{min-height:44px;font-size:14px;overflow-wrap:anywhere}.obs-market{grid-template-columns:1fr;gap:8px}.obs-detail-filters{flex-wrap:wrap}.obs-detail h2{overflow-wrap:anywhere}}
</style>

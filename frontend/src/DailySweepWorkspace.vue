<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { api, beijingDateTime } from './api';
import { toCsv } from './domain';
import { renderShareImages } from './dailyShare';
import { selectedOdds, currentSelectedOdds } from './dailyOdds';
const predictionOdds = (row, code) => selectedOdds(row.forecast?.market_assessments?.find(m => m.code === code));
const date = ref(''), source = ref('auto'), search = ref(''), data = ref(null), error = ref(''), loading = ref(false), selected = ref(null);
const codes = ['had', 'hhad', 'hafu', 'crs', 'ttg'];
const labels = { had: '胜平负', hhad: '让球', hafu: '半全场', crs: '比分', ttg: '进球数' };
const sources = { model: '足球模型', market: '市场参考', legacy: '旧日志参考' };
const shareOpen = ref(false), shareBusy = ref(false), shareError = ref(''), shareImages = ref([]), shareIndex = ref(0);
let disposed = false;
function clearShare() { shareImages.value.forEach(i=>URL.revokeObjectURL(i.url));shareImages.value=[]; }
async function generateShare() {
  if (!data.value?.rows.length || shareBusy.value) return;
  const snapshot = JSON.parse(JSON.stringify(data.value));
  clearShare(); shareIndex.value=0; shareError.value='';shareBusy.value=true;shareOpen.value=true;
  try { const images=await renderShareImages(snapshot);if(!disposed)shareImages.value=images.map(i=>({...i,url:URL.createObjectURL(i.blob)})); }
  catch(e) { shareError.value=e.message || '生成失败，请重试'; }
  finally { shareBusy.value=false; }
}
function downloadShare() {
  const image=shareImages.value[shareIndex.value];if(!image)return;
  const a=document.createElement('a');a.href=image.url;a.download=image.name;a.click();
}
const exploration = (row,code) => row.forecast?.explorations?.find(m=>m.code===code);
const explorationResult = (row,code) => row.exploration_results?.find(m=>m.code===code);
const rows = computed(() => (data.value?.rows || []).filter(r => `${r.number}${r.league}${r.home}${r.away}`.includes(search.value.trim())));
const cell = (row, code) => row.markets?.find(m => m.code === code) || {};
const summaryChoice = ref(''), page = ref(1), pageSize = 10;
const summaryKinds = computed(() => source.value !== 'auto' ? [source.value] : Object.keys(sources).filter(kind => (data.value?.rows || []).some(r => r.forecast?.kind === kind)));
const summaryKind = computed(() => summaryKinds.value.includes(summaryChoice.value) ? summaryChoice.value : summaryKinds.value[0]);
const summaryValues = computed(() => data.value?.summary?.[summaryKind.value] || {});
const visibleRows = computed(() => rows.value.slice((page.value-1)*pageSize, page.value*pageSize));
const percent = (hits, total) => total ? `${(100*hits/total).toFixed(1)}%` : '待统计';
function matchStats(row) {
  const settled = (row.markets || []).filter(m => typeof m.hit === 'boolean');
  const hits = settled.filter(m => m.hit).length;
  return { hits, total: settled.length, rate: percent(hits, settled.length) };
}
function predictionLabel(row) {
  return row.forecast?.kind === 'model' ? '模型预测' : row.forecast?.kind === 'legacy' ? '原预测' : '赛前参考';
}
function selection(row, code) {
  const value = cell(row, code).selection;
  return !value || value === '未归档' ? (row.forecast?.kind === 'legacy' ? '当时未记录' : '暂无预测') : value;
}
function probability(row, code) {
  const market = row.forecast?.market_assessments?.find(m => m.code === code);
  return market?.selected_keys?.length && typeof market.coverage === 'number' && Number.isFinite(market.coverage)
    ? `${market.coverage.toFixed(1)}%` : null;
}
function resultClass(row, code) {
  const hit = cell(row, code).hit;
  return hit === true ? 'is-hit' : hit === false ? 'is-miss' : 'is-pending';
}
function resultText(row, code) {
  const m = cell(row, code);
  return m.hit === true ? '✓ 命中' : m.hit === false ? '× 未命中' : m.status === '未归档选择' ? '无预测' : m.status || '待赛果';
}
let timer, sequence = 0;
async function load() {
  const token = ++sequence;
  loading.value = true;
  try {
    const value = await api.dailySweep(date.value, source.value);
    if (token !== sequence) return;
    data.value = value; error.value = '';
  } catch (e) { if (token === sequence) error.value = e.message; }
  finally { if (token === sequence) loading.value = false; }
}
function exportRows() {
  const output = [['比赛日', '场次', '赛事', '主队', '客队', '开球时间', '判断来源', '记录时间', '官方赔率核验时间', ...codes.flatMap(c => [labels[c]+'选择', labels[c]+'最新SP', labels[c]+'留档SP', labels[c]+'进取观察SP', labels[c]+'赛果', labels[c]+'命中']), '正式比分'],
    ...rows.value.map(r => [r.day, r.number, r.league, r.home, r.away, beijingDateTime(r.kickoff), r.forecast?.source || '未归档', r.forecast?.captured_at || '', r.current_quotes?.checked_at || '', ...codes.flatMap(c => { const m=cell(r,c); return [m.selection, currentSelectedOdds(r,c), predictionOdds(r,c), selectedOdds(exploration(r,c)), m.actual, m.status]; }), r.score || '待赛果'])];
  const url = URL.createObjectURL(new Blob([toCsv(output.slice(1), output[0].map((title,key)=>({title,key})))], {type:'text/csv;charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = `每日扫盘-${data.value.day}.csv`; a.click(); URL.revokeObjectURL(url);
}
watch([date, source], load);
watch([date, source, search], () => { page.value = 1; });
watch(() => rows.value.length, n => { page.value = Math.min(page.value, Math.max(1, Math.ceil(n/pageSize))); });
onMounted(() => { load(); timer = setInterval(() => { if (!document.hidden) load(); }, 15000); });
onBeforeUnmount(() => { disposed=true; clearInterval(timer); sequence++; clearShare(); });
</script>

<template>
  <section class="daily-workspace" aria-label="每日扫盘命中">
    <div class="page-heading"><h1>每日扫盘命中</h1><div class="share-actions"><t-button variant="outline" :loading="loading" @click="load">刷新记录</t-button><t-button theme="primary" :loading="shareBusy" :disabled="loading || !data?.rows?.length" @click="generateShare">生成分享图</t-button></div></div>
    <div v-if="error" role="alert" class="panel daily-alert">{{ error }} <t-button variant="text" @click="load">重试</t-button></div>
    <div class="panel daily-filters">
      <label>竞彩比赛日<t-date-picker :value="date || data?.day || ''" :clearable="false" @change="value => date = value" /></label>
      <label>判断来源<t-select v-model="source" :options="[{label:'全部（模型优先）',value:'auto'}, ...Object.entries(sources).map(([value,label])=>({value,label}))]" /></label>
      <label>搜索比赛<t-input v-model="search" placeholder="球队、赛事或场次" clearable /></label>
      <t-button variant="outline" :disabled="!rows.length" @click="exportRows">导出记录</t-button>
    </div>
    <section class="panel daily-summary" aria-label="各来源命中汇总">
      <div class="daily-summary-heading">
        <div><h2>当日命中率 <span class="summary-source">{{ sources[summaryKind] || '暂无记录' }}</span></h2><p class="daily-note">{{ data?.day || date }} · 按玩法统计，预测来源分别核对</p></div>
        <div v-if="summaryKinds.length > 1" class="summary-switch" aria-label="命中率来源">
          <button v-for="kind in summaryKinds" :key="kind" :aria-pressed="summaryKind === kind" @click="summaryChoice = kind">{{ sources[kind] }}</button>
        </div>
      </div>
      <div class="daily-rate-grid">
        <div v-for="code in codes" :key="code" class="daily-rate" :aria-label="`${labels[code]}命中率`">
          <span>{{ labels[code] }}</span>
          <strong :class="{ 'rate-pending': !summaryValues[code]?.settled }">{{ percent(summaryValues[code]?.hits, summaryValues[code]?.settled) }}</strong>
          <span class="daily-note">{{ summaryValues[code]?.settled ? `命中 ${summaryValues[code].hits} / 已核对 ${summaryValues[code].settled}` : '尚无可统计赛果' }}</span>
          <div class="rate-track" aria-hidden="true"><span :style="{ width: `${summaryValues[code]?.settled ? 100*summaryValues[code].hits/summaryValues[code].settled : 0}%` }" /></div>
        </div>
      </div>
    </section>
    <div class="daily-list-heading"><h2>逐场预测对比 <span class="daily-note">{{ rows.length }} 场</span></h2><span class="daily-note" v-if="loading">正在更新…</span><span v-else-if="data?.collector" class="daily-note" role="status">{{ data.collector.status === 'error' ? data.collector.error : '自动记录中' }}</span></div>
    <div v-if="!rows.length" class="panel empty-state">{{ loading ? '正在读取扫盘记录…' : '这一天尚无匹配记录，请更换日期或筛选条件。' }}</div>
    <div class="daily-match-list" :aria-busy="loading">
      <article v-for="row in visibleRows" :key="row.id" class="panel daily-match" :aria-label="`${row.home}对${row.away}`">
        <header class="daily-match-heading">
          <div class="fixture-info"><div class="fixture-meta"><span>{{ row.number }} · {{ row.league }}</span><span class="source-chip" :class="{ 'source-model': row.forecast?.kind === 'model' }">{{ row.forecast?.kind === 'model' ? '足球模型预测' : row.forecast?.source || '未取得预测' }}</span></div>
            <h3><a :href="`#/match/${encodeURIComponent(row.id)}/analysis`">{{ row.home }} <span class="versus">vs</span> {{ row.away }}</a></h3>
            <p class="daily-note">{{ beijingDateTime(row.kickoff) }} 开球</p>
          </div>
          <div class="fixture-score"><span class="daily-note">正式比分</span><strong>{{ row.cancelled ? '已取消' : row.score || '待赛果' }}</strong></div>
          <div class="fixture-rate"><span class="daily-note">本场玩法命中率</span><strong :class="{ 'rate-pending': !matchStats(row).total }">{{ matchStats(row).rate }}</strong><span class="daily-note">{{ matchStats(row).total ? `${matchStats(row).hits} / ${matchStats(row).total} 项已核对` : '等待有效对照' }}</span></div>
          <t-button variant="text" @click="selected = row">查看记录</t-button>
        </header>
        <div class="daily-comparison-grid">
          <section v-for="code in codes" :key="code" class="market-comparison" :class="resultClass(row,code)" :aria-label="`${labels[code]}对比`">
            <div class="market-heading"><h4>{{ labels[code] }}<small v-if="code === 'hhad' && cell(row,code).line != null"> ({{ cell(row,code).line > 0 ? '+' : '' }}{{ cell(row,code).line }})</small></h4><span class="result-badge">{{ resultText(row,code) }}</span></div>
            <div class="prediction-value"><span class="daily-note">{{ predictionLabel(row) }}</span><strong :class="{ 'no-selection': ['当时未记录','暂无预测'].includes(selection(row,code)) }">{{ selection(row,code) }}</strong></div>
            <p v-if="currentSelectedOdds(row,code)" class="odds-note current-odds"><b>{{ currentSelectedOdds(row,code) }}</b><span>官方核验 {{ beijingDateTime(row.current_quotes?.checked_at) }}</span><a :href="`#/match/${encodeURIComponent(row.id)}/odds`">查看赔率走势</a></p>
            <details v-if="predictionOdds(row,code)" class="odds-note"><summary>预测时SP</summary><span>{{ predictionOdds(row,code) }}</span></details>
            <div class="actual-value"><span class="daily-note">实际赛果</span><strong>{{ cell(row,code).actual === '—' ? '待确认' : cell(row,code).actual || '待确认' }}</strong></div>
            <p v-if="probability(row,code)" class="probability-note">{{ row.forecast.kind === 'model' ? '模型覆盖概率' : '参考覆盖概率' }} <b>{{ probability(row,code) }}</b></p>
            <div v-if="exploration(row,code)" class="exploration"><span>进取观察 · {{ exploration(row,code).coverage.toFixed(1) }}%</span><strong>{{ exploration(row,code).selection }}</strong><span>{{ currentSelectedOdds(row,code,true) }}</span><details><summary>观察参考SP</summary>{{ selectedOdds(exploration(row,code)) }}</details><span v-if="explorationResult(row,code)?.hit != null">{{ explorationResult(row,code).hit ? '观察命中' : '观察未命中' }}</span></div>
          </section>
        </div>
      </article>
    </div>
    <div v-if="rows.length > pageSize" class="daily-pagination"><t-pagination v-model="page" :total="rows.length" :page-size="pageSize" :show-page-size="false" :max-page-btn="3" /></div>
    <details class="daily-rules"><summary>统计口径与自动记录说明</summary><p>命中率 = 命中数 ÷ 已核对数。观望、未记录、待赛果、取消及冲突不计入分母。多项选择的命中表示覆盖赛果；赛前覆盖概率不等于赛后命中率。</p><p>按竞彩比赛日归档，包含次日凌晨比赛。模型预测、市场参考、旧日志分别统计；市场参考和旧日志不标成 AI 模型预测。赛前选择保留，赛果修正追加记录。</p><p>进取观察从已归档概率中选择主选以外的大比分、高进球数或半全场变化，概率通常较低，独立核对，不并入上方主预测命中率。仅开赛前记录；无足够概率支持时不强行给出。该观察规则尚未经过独立效果评估。</p><p v-if="data?.collector">服务运行期间每分钟检查 · 最近成功 {{ beijingDateTime(data.collector.last_success) }}</p></details>
    <t-dialog v-model:visible="shareOpen" header="每日扫盘分享图" width="min(1100px, calc(100vw - 32px))" :footer="false" :close-on-overlay-click="!shareBusy" :close-btn="!shareBusy" :close-on-esc-keydown="!shareBusy">
      <div class="share-preview">
        <p class="daily-note">包含所选日期与来源的全部比赛，不受搜索或分页影响。每张最多12场，图片在本地生成。</p>
        <p v-if="shareBusy" role="status">正在生成高清图片…</p>
        <p v-if="shareError" role="alert">{{ shareError }} <t-button @click="generateShare">重新生成</t-button></p>
        <template v-if="shareImages.length">
          <div class="share-actions"><t-button :disabled="shareIndex===0" variant="outline" @click="shareIndex--">上一张</t-button><span>{{ shareIndex+1 }} / {{ shareImages.length }} 张</span><t-button :disabled="shareIndex===shareImages.length-1" variant="outline" @click="shareIndex++">下一张</t-button><t-button theme="primary" @click="downloadShare">下载当前图片</t-button></div>
          <img :src="shareImages[shareIndex].url" :alt="`每日扫盘分享图，第${shareIndex+1}张`" />
        </template>
      </div>
    </t-dialog>
    <t-drawer :visible="!!selected" header="扫盘留档与复盘" size="680px" :footer="false" @update:visible="v => { if (!v) selected = null }">
      <template v-if="selected">
        <h2>{{ selected.home }} vs {{ selected.away }}</h2>
        <p>比赛日 {{ selected.day }} · {{ selected.league }}</p>
        <p>来源：{{ selected.forecast?.source || '未取得赛前判断' }} · 记录于 {{ beijingDateTime(selected.forecast?.captured_at) || '—' }}</p>
        <p v-if="selected.forecast?.notice">{{ selected.forecast.notice }}</p>
        <p v-if="selected.forecast?.imported_at">旧记录于 {{ beijingDateTime(selected.forecast.imported_at) }} 导入，仅保留原已记录选择。</p>
        <p v-if="selected.forecast?.model_version">模型：{{ selected.forecast.model_version }}</p>
        <p v-else>模型状态：{{ selected.model_status }}</p>
        <p>已留存来源：{{ selected.available_sources.map(k => sources[k]).join('、') || '暂无' }}。可在页面来源筛选中分别查看。</p>
        <p v-for="market in selected.forecast?.market_assessments || []" :key="market.code">{{ labels[market.code] }}：{{ market.selection }} · 首次记录 {{ beijingDateTime(market.captured_at || selected.forecast.captured_at) }}</p>
        <p>SP为选择所用的归档报价，不是当前赔率；缺失的历史报价不以新赔率补填。进取观察沿用原概率快照中的报价。</p>
        <p v-for="market in selected.forecast?.market_assessments || []" :key="`odds-${market.code}`">{{ labels[market.code] }}留档SP：{{ selectedOdds(market) || '无所选项' }}</p>
        <p v-for="market in selected.forecast?.explorations || []" :key="market.code">进取观察 · {{ labels[market.code] }}：{{ market.selection }}（{{ market.coverage }}%）· {{ beijingDateTime(market.captured_at) }} 记录 · {{ market.reason }}</p>
        <a :href="`#/reviews/${encodeURIComponent(selected.id)}`">查看关联复盘与赛后分析</a>
        <h3>赛果核对历史</h3>
        <div v-for="version in selected.settlement_versions" :key="version.id" class="daily-version">
          <p>{{ beijingDateTime(version.created_at) }} · {{ version.source || '等待正式赛果' }} · {{ version.score || '待赛果' }}</p>
          <p v-for="c in codes" :key="c">{{ labels[c] }}：{{ cell(version,c).selection }} → {{ cell(version,c).actual }} · {{ cell(version,c).status }}</p>
        </div>
        <p v-if="!selected.settlement_versions.length">没有有效的赛前留档，无法事后补写预测。</p>
      </template>
    </t-drawer>
  </section>
</template>

<style scoped>
.daily-workspace{min-width:0;--hit:#06705c;--miss:#b3261e;--muted:#606b7a;overflow-wrap:anywhere}
.daily-filters{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1.4fr) auto;gap:20px;align-items:end;padding:20px;margin-bottom:20px}
.daily-filters label{display:grid;gap:8px;min-width:0}.daily-filters :deep(.t-select__wrap){width:100%;min-width:0}.daily-note{font-size:12px;color:var(--muted);font-weight:400;margin:0;line-height:1.6}
.daily-summary{padding:20px;margin-bottom:24px}.daily-summary-heading,.daily-list-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.daily-summary-heading h2{margin:0 0 4px}.summary-source{font-size:13px;font-weight:400;color:var(--muted);margin-left:12px}
.summary-switch{display:flex;flex-wrap:wrap;gap:8px}.summary-switch button{border:1px solid var(--border);background:#fff;color:var(--muted);padding:8px 12px;cursor:pointer}.summary-switch button[aria-pressed=true]{color:var(--blue);border-color:var(--blue);background:#ecf2fe}
.daily-rate-grid,.daily-comparison-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.daily-rate-grid{margin-top:20px;gap:20px}.daily-rate{display:flex;flex-direction:column;gap:7px;min-width:0}.daily-rate>strong{font-size:32px;line-height:1.25;font-variant-numeric:tabular-nums;color:var(--blue)}.daily-rate>strong.rate-pending{font-size:24px;color:var(--muted)}.rate-track{height:4px;background:#edf0f5;margin-top:4px}.rate-track span{height:100%;display:block;background:var(--blue)}
.daily-list-heading{margin-bottom:14px}.daily-list-heading h2{margin:0}.daily-match-list{display:grid;gap:18px}.daily-match{min-width:0;padding:20px}.daily-match-heading{display:flex;align-items:center;gap:24px;flex-wrap:wrap;margin-bottom:18px}.fixture-info{flex:1;min-width:180px}.fixture-meta{display:flex;align-items:center;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--muted)}.fixture-info h3{font-size:18px;line-height:1.5;margin:8px 0 4px}.fixture-info h3 a{color:inherit}.versus{font-size:13px;color:var(--muted);margin:0 6px;font-weight:400}.source-chip{padding:3px 7px;background:#eef1f5;color:#4b5664}.source-model{color:var(--blue);background:#ecf2fe;font-weight:600}.fixture-score,.fixture-rate{display:flex;flex-direction:column;gap:3px}.fixture-score strong{font-size:24px}.fixture-rate strong{font-size:24px;color:var(--blue)}.fixture-rate strong.rate-pending{color:var(--muted);font-size:18px}
.market-comparison{min-width:0;border:1px solid var(--border);border-top:3px solid #b8c1cc;padding:12px;background:#fafbfc}.market-comparison.is-hit{border-top-color:var(--hit);background:#f3faf7}.market-comparison.is-miss{border-top-color:var(--miss);background:#fff8f7}.market-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;flex-wrap:wrap}.market-heading h4{font-size:14px;margin:0;line-height:22px}.market-heading small{font-weight:400}.result-badge{font-size:12px;font-weight:600;line-height:22px;padding:0 6px;background:#e9edf2;color:#4b5664}.is-hit .result-badge{color:var(--hit);background:#d9efe5}.is-miss .result-badge{color:var(--miss);background:#fce5e3}
.prediction-value{display:flex;flex-direction:column;gap:5px;margin:14px 0 12px}.prediction-value strong{font-size:18px;color:var(--blue);line-height:1.5}.prediction-value .no-selection{font-size:14px;color:var(--muted);font-weight:400}.actual-value{display:flex;align-items:baseline;justify-content:space-between;gap:10px;padding-top:10px;border-top:1px solid #e1e6ed}.actual-value strong{font-size:16px;text-align:right}.probability-note{margin:10px 0 0;font-size:12px;line-height:1.5;color:var(--muted)}.probability-note b{color:var(--blue)}
.daily-pagination{margin-top:20px}.daily-rules{font-size:12px;line-height:1.7;color:var(--muted);margin-top:20px}.daily-rules summary{cursor:pointer;min-height:32px}.daily-version{border-top:1px solid var(--border);margin-top:20px;padding-top:8px}.daily-alert{padding:16px;color:var(--miss)}:deep(.t-drawer){max-width:100vw;overflow-wrap:anywhere}
@media(max-width:1000px){.daily-comparison-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.daily-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){.daily-match,.daily-summary,.daily-filters{padding:16px}.daily-filters{gap:12px}.daily-filters label:nth-child(3){grid-column:1/-1}.daily-filters :deep(.t-date-picker),.daily-filters :deep(.t-select){width:100%;min-width:0}.daily-rate-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 16px}.daily-rate>strong{font-size:28px}.daily-rate:last-child{grid-column:1/-1}.daily-comparison-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.market-comparison:last-child{grid-column:1/-1}.daily-match-heading{gap:14px}.fixture-info{flex-basis:100%;min-width:0}.fixture-score,.fixture-rate{flex:1}.fixture-info h3{font-size:17px}.market-comparison{padding:10px}.prediction-value strong{font-size:16px}.actual-value{flex-wrap:wrap}.summary-source{display:block;margin:4px 0 0}.daily-pagination :deep(.t-pagination){flex-wrap:wrap;gap:12px}}
</style>

<style scoped>
.daily-workspace{--blue:#315d80;--hit:#137d80;--miss:#a86d32;--muted:#65758a;color:#25384d}
.odds-note{display:grid;gap:4px;font-size:12px;line-height:1.6;color:var(--muted);margin:0 0 12px;overflow-wrap:anywhere}.odds-note b{font-weight:500;color:#3c5369;font-variant-numeric:tabular-nums}
.current-odds b{font-size:14px;color:#294e6c}.odds-note summary,.exploration summary{cursor:pointer}
.daily-summary{border-color:#dce5ec;background:#fff}.daily-rate>strong,.fixture-rate strong{color:#315d80}.rate-track{background:#edf2f6}.rate-track span{background:#558e9e}
.daily-match{border-color:#dce5ec}.market-comparison,.market-comparison.is-hit,.market-comparison.is-miss{border:1px solid #e1e8ee;border-top:1px solid #e1e8ee;background:#f8fafc}
.market-comparison.is-hit{background:#f5faf9}.market-comparison.is-miss{background:#fcfaf7}.result-badge{background:#edf1f5;color:#697789;font-weight:500}.is-hit .result-badge{background:#e1f1ed;color:#146e65}.is-miss .result-badge{background:#f5eadc;color:#94612d}
.prediction-value strong{color:#294e6c}.actual-value{border-color:#e3eaf0}.source-chip{background:#eef3f7;color:#586d80}.source-model{background:#e6f0f4;color:#315d80}.daily-match-heading{border-bottom:1px solid #edf1f5;padding-bottom:16px}.market-heading h4{color:#3c5369}.probability-note b{color:#397585}
.share-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.share-preview{min-width:0}.share-preview img{display:block;width:100%;height:auto;margin-top:16px;border:1px solid #dce5ec}.share-preview .share-actions{margin-top:16px}.daily-workspace :deep(.t-button--theme-primary){background:#315d80;border-color:#315d80}.exploration{border-top:1px dashed #dbc6a8;margin-top:12px;padding-top:10px;display:flex;flex-direction:column;gap:4px;color:#94612d;font-size:12px}.exploration strong{font-size:16px;font-weight:600}.daily-workspace .page-heading{flex-wrap:wrap}
@media(max-width:600px){.page-heading .share-actions{gap:8px}.share-preview .share-actions{gap:8px}}
</style>

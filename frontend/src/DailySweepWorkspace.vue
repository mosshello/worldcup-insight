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
const reportTab = ref('sources'), rulesOpen = ref(false);
const competition = ref('');
const summaryKinds = computed(() => source.value !== 'auto' ? [source.value] : Object.keys(sources));
const summaryKind = computed(() => summaryKinds.value.includes(summaryChoice.value) ? summaryChoice.value : summaryKinds.value[0]);
const summaryValues = computed(() => data.value?.audit?.sources?.[summaryKind.value] || {});
const pairedValues = computed(() => data.value?.audit?.paired || {});
const metric = value => typeof value === 'number' ? value.toFixed(3) : '—';
const selectionCount = (row, code) => row.forecast?.market_assessments?.find(m => m.code === code)?.selected_keys?.length || 0;
const visibleRows = computed(() => rows.value.slice((page.value-1)*pageSize, page.value*pageSize));
const percent = (hits, total) => total ? `${(100*hits/total).toFixed(1)}%` : '待统计';
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
  return m.hit === true ? (selectionCount(row, code) > 1 ? '已覆盖' : '命中') : m.hit === false ? (selectionCount(row, code) > 1 ? '未覆盖' : '未命中') : m.status === '未归档选择' ? '无预测' : m.status || '待赛果';
}
let timer, sequence = 0;
async function load() {
  const token = ++sequence;
  loading.value = true;
  try {
    const value = await api.dailySweep(date.value, source.value, competition.value);
    if (token !== sequence) return;
    data.value = value; error.value = '';
  } catch (e) { if (token === sequence) error.value = e.message; }
  finally { if (token === sequence) loading.value = false; }
}
function exportRows() {
  const output = [['比赛日', '场次', '赛事', '主队', '客队', '开球时间', '判断来源', '记录时间', '官方赔率核验时间', ...codes.flatMap(c => [labels[c]+'选择', labels[c]+'选项数', labels[c]+'记录时间', labels[c]+'最新SP', labels[c]+'留档SP', labels[c]+'进取观察SP', labels[c]+'赛果', labels[c]+'核对']), '正式比分'],
    ...rows.value.map(r => [r.day, r.number, r.league, r.home, r.away, beijingDateTime(r.kickoff), r.forecast?.source || '未归档', r.forecast?.captured_at || '', r.current_quotes?.checked_at || '', ...codes.flatMap(c => { const m=cell(r,c), p=r.forecast?.market_assessments?.find(m=>m.code===c); return [m.selection,selectionCount(r,c),p?.captured_at || r.forecast?.captured_at || '', currentSelectedOdds(r,c), predictionOdds(r,c), selectedOdds(exploration(r,c)), m.actual, resultText(r,c)]; }), r.score || '待赛果'])];
  const url = URL.createObjectURL(new Blob([toCsv(output.slice(1), output[0].map((title,key)=>({title,key})))], {type:'text/csv;charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = `每日扫盘-${data.value.day}.csv`; a.click(); URL.revokeObjectURL(url);
}
watch([date, source, competition], load);
watch([date, source, competition, search], () => { page.value = 1; });
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
      <label>赛事<t-select v-model="competition" :options="[{label:'全部赛事',value:''}, ...(data?.competitions || [])]" /></label>
      <label>搜索比赛<t-input v-model="search" placeholder="球队、赛事或场次" clearable /></label>
      <t-button variant="outline" :disabled="!rows.length" @click="exportRows">导出记录</t-button>
    </div>
    <section class="panel daily-summary" aria-label="各来源命中汇总">
      <div class="daily-summary-heading">
        <t-radio-group v-model="reportTab" theme="button" variant="default-filled" :options="[{label:'分类统计',value:'sources'},{label:'同期对照',value:'paired'}]" />
        <t-button variant="text" @click="rulesOpen = true">统计口径</t-button>
      </div>
      <template v-if="reportTab === 'sources'">
        <div class="summary-switch" aria-label="统计来源"><t-button v-for="kind in summaryKinds" :key="kind" :variant="summaryKind === kind ? 'base' : 'outline'" :theme="summaryKind === kind ? 'primary' : 'default'" :aria-pressed="summaryKind === kind" @click="summaryChoice = kind">{{ sources[kind] }}</t-button></div>
        <div class="daily-rate-grid">
          <section v-for="code in codes" :key="code" class="daily-rate" :aria-label="`${labels[code]}统计`">
            <h3>{{ labels[code] }}</h3>
            <div v-for="group in ['single','multiple']" :key="group" class="audit-metric"><span>{{ group === 'single' ? '单选命中' : '多选覆盖' }}</span><strong>{{ percent(summaryValues[code]?.[group]?.hits, summaryValues[code]?.[group]?.settled) }}</strong><small>{{ summaryValues[code]?.[group]?.hits || 0 }} / {{ summaryValues[code]?.[group]?.settled || 0 }}</small></div>
            <dl class="audit-counts"><div><dt>无预测</dt><dd>{{ summaryValues[code]?.missing ?? '—' }}</dd></div><div><dt>观望</dt><dd>{{ summaryValues[code]?.abstained ?? '—' }}</dd></div><div><dt>待赛果</dt><dd>{{ summaryValues[code]?.pending ?? '—' }}</dd></div><div><dt>排除</dt><dd>{{ summaryValues[code]?.excluded ?? '—' }}</dd></div></dl>
          </section>
        </div>
      </template>
      <template v-else>
        <div class="paired-heading"><h2>模型 / 市场</h2><span class="daily-note">同场、同期快照 · 误差越低越好</span></div>
        <div class="daily-rate-grid">
          <section v-for="code in codes" :key="code" class="daily-rate" :aria-label="`${labels[code]}同期对照`">
            <h3>{{ labels[code] }}</h3><p class="daily-note">已核对 {{ pairedValues[code]?.settled || 0 }} / {{ pairedValues[code]?.eligible || 0 }} 场</p>
            <div class="paired-metric"><span>Brier</span><strong>{{ metric(pairedValues[code]?.model?.brier) }} / {{ metric(pairedValues[code]?.market?.brier) }}</strong></div>
            <div class="paired-metric"><span>Log loss</span><strong>{{ metric(pairedValues[code]?.model?.log_loss) }} / {{ metric(pairedValues[code]?.market?.log_loss) }}</strong></div>
          </section>
        </div>
      </template>
    </section>
    <div class="daily-list-heading"><h2>逐场预测对比 <span class="daily-note">{{ rows.length }} 场</span></h2><span class="daily-note" v-if="loading">正在更新…</span><span v-else-if="data?.collector" class="daily-note" role="status">{{ data.collector.status === 'error' ? data.collector.error : '自动记录中' }}</span></div>
    <div v-if="!rows.length" class="panel empty-state">{{ loading ? '正在读取扫盘记录…' : '这一天尚无匹配记录，请更换日期或筛选条件。' }}</div>
    <div class="daily-match-list" :aria-busy="loading">
      <article v-for="row in visibleRows" :key="row.id" class="panel daily-match" :aria-label="`${row.home}对${row.away}`">
        <header class="daily-match-heading">
          <div class="fixture-info"><div class="fixture-meta"><span>{{ row.number }} · {{ row.league }}</span><span class="source-chip" :class="{ 'source-model': row.forecast?.kind === 'model' }">{{ row.forecast?.kind === 'model' ? '足球模型预测' : row.forecast?.source || '未取得预测' }}</span></div>
            <h3><a :href="`#/match/${encodeURIComponent(row.id)}/analysis`">{{ row.home }} <span class="versus">vs</span> {{ row.away }}</a></h3>
            <p class="daily-note">{{ beijingDateTime(row.kickoff) }} 开球</p>
            <p v-if="row.forecast" class="daily-note">{{ beijingDateTime(row.forecast.captured_at) }} 留档</p>
          </div>
          <div class="fixture-score"><span class="daily-note">正式比分</span><strong>{{ row.cancelled ? '已取消' : row.score || '待赛果' }}</strong></div>
          <div class="fixture-links"><a :href="`#/match/${encodeURIComponent(row.id)}/odds`">赔率走势</a><t-button variant="text" @click="selected = row">查看记录</t-button><span v-if="row.current_quotes?.checked_at" class="daily-note">报价核验 {{ beijingDateTime(row.current_quotes.checked_at) }}</span></div>
        </header>
        <div class="daily-comparison-grid">
          <section v-for="code in codes" :key="code" class="market-comparison" :class="resultClass(row,code)" :aria-label="`${labels[code]}对比`">
            <div class="market-heading"><h4>{{ labels[code] }}<small v-if="code === 'hhad' && cell(row,code).line != null"> ({{ cell(row,code).line > 0 ? '+' : '' }}{{ cell(row,code).line }})</small></h4><span class="result-badge">{{ resultText(row,code) }}</span></div>
            <div class="prediction-value"><span class="daily-note">{{ predictionLabel(row) }}<template v-if="selectionCount(row,code)"> · {{ selectionCount(row,code) }} 项</template></span><strong :class="{ 'no-selection': ['当时未记录','暂无预测'].includes(selection(row,code)) }">{{ selection(row,code) }}</strong></div>
            <p v-if="currentSelectedOdds(row,code)" class="odds-note current-odds"><b>{{ currentSelectedOdds(row,code) }}</b></p>
            <details v-if="predictionOdds(row,code)" class="odds-note"><summary>预测时SP</summary><span>{{ predictionOdds(row,code) }}</span></details>
            <div class="actual-value"><span class="daily-note">实际赛果</span><strong>{{ cell(row,code).actual === '—' ? '待确认' : cell(row,code).actual || '待确认' }}</strong></div>
            <p v-if="probability(row,code)" class="probability-note">{{ row.forecast.kind === 'model' ? '模型覆盖概率' : '参考覆盖概率' }} <b>{{ probability(row,code) }}</b></p>
            <div v-if="exploration(row,code)" class="exploration"><span>进取观察 · {{ exploration(row,code).coverage.toFixed(1) }}%</span><strong>{{ exploration(row,code).selection }}</strong><span>{{ currentSelectedOdds(row,code,true) }}</span><details><summary>观察参考SP</summary>{{ selectedOdds(exploration(row,code)) }}</details><span v-if="explorationResult(row,code)?.hit != null">{{ explorationResult(row,code).hit ? '观察命中' : '观察未命中' }}</span></div>
          </section>
        </div>
      </article>
    </div>
    <div v-if="rows.length > pageSize" class="daily-pagination"><t-pagination v-model="page" :total="rows.length" :page-size="pageSize" :show-page-size="false" :max-page-btn="3" /></div>
    <t-drawer v-model:visible="rulesOpen" header="统计口径" size="520px" :footer="false"><div class="audit-rules"><p>统计覆盖所选比赛日的全部场次，不受球队搜索或分页影响。各来源独立统计，不按“模型优先”隐去市场样本。</p><p>单选命中：选1项且命中；多选覆盖：选多项且包含赛果。分母只包含已核对场次。无预测、观望、待赛果、取消和冲突分开计数。</p><p>同期对照只使用足球模型归档内同时保留的市场参考，报价核验时间须在预测前180秒内。同场、同玩法、同让球线、同一正式赛果。缺少同期概率的记录不纳入，也不用当前赔率补填。</p><p>Brier为完整概率向量的平方误差之和；Log loss为实际结果的负对数概率。两者越低越好。不同玩法、赛事、预测提前量不能直接合并评价优劣；当前只是当日描述性对照。</p><p>旧日志与进取观察不进入模型对照；观察选择属于固定规则，不代表AI决策或验证有效的策略。</p><p>赛前留档不改写。AI试验须单独记录，不计入现有模型成绩。</p><p v-if="data?.collector">最近记录 {{ beijingDateTime(data.collector.last_success) }}</p></div></t-drawer>
    <t-dialog v-model:visible="shareOpen" header="每日扫盘分享图" width="min(1100px, calc(100vw - 32px))" :footer="false" :close-on-overlay-click="!shareBusy" :close-btn="!shareBusy" :close-on-esc-keydown="!shareBusy">
      <div class="share-preview">
        <p class="daily-note">包含所选日期、赛事与来源，不受搜索或分页影响。</p>
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
.daily-workspace{min-width:0;--hit:var(--td-success-color,#008858);--miss:var(--td-error-color,#d54941);overflow-wrap:anywhere}
.daily-workspace .page-heading,.share-actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
.fixture-links{display:grid;grid-template-columns:auto auto;gap:4px 16px;align-items:center}.fixture-links .daily-note{grid-column:1/-1}.fixture-links>a{min-height:32px;display:flex;align-items:center}
.daily-filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) minmax(0,1.2fr) auto;gap:20px;align-items:end;padding:24px;margin-bottom:24px}
.daily-filters label{display:grid;gap:8px;min-width:0}.daily-filters :deep(.t-select__wrap){width:100%;min-width:0}
.daily-note{font-size:12px;color:var(--muted);font-weight:400;margin:0;line-height:1.6}
.daily-summary{margin-bottom:24px}.daily-summary-heading,.daily-list-heading,.paired-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.daily-summary-heading{padding:16px 24px;border-bottom:1px solid var(--border)}.summary-switch{display:flex;gap:12px;flex-wrap:wrap;padding:20px 24px}
.daily-rate-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr))}.daily-rate{min-width:0;padding:20px 24px;border-right:1px solid var(--border)}.daily-rate:last-child{border-right:0}.daily-rate h3{font-size:14px;margin:0 0 16px}.audit-metric{display:grid;grid-template-columns:1fr auto;gap:4px 8px;margin-bottom:14px;font-size:12px;color:var(--muted)}.audit-metric strong{font-size:18px;font-weight:600;color:var(--td-text-color-primary,#1f2329);font-variant-numeric:tabular-nums}.audit-metric small{grid-column:2;text-align:right;font-size:12px}
.audit-counts{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0;padding-top:12px;border-top:1px solid var(--border);font-size:12px;color:var(--muted)}.audit-counts div{display:flex;gap:6px;justify-content:space-between}.audit-counts dd{margin:0;color:var(--td-text-color-primary,#1f2329)}
.paired-heading{padding:20px 24px 0}.paired-heading h2{font-size:16px}.paired-metric{display:grid;gap:8px;margin-top:16px;font-size:12px;color:var(--muted)}.paired-metric strong{font-weight:500;color:var(--td-text-color-primary,#1f2329);font-size:16px;font-variant-numeric:tabular-nums}
.daily-list-heading{margin-bottom:16px}.daily-list-heading h2{margin:0}.daily-match-list{display:grid;gap:24px}.daily-match{min-width:0}.daily-match-heading{display:flex;align-items:center;gap:24px;flex-wrap:wrap;padding:20px 24px;border-bottom:1px solid var(--border)}.fixture-info{flex:1;min-width:180px}.fixture-meta{display:flex;align-items:center;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted)}.fixture-info h3{font-size:18px;line-height:1.5;margin:8px 0}.fixture-info h3 a{color:inherit}.versus{font-size:13px;color:var(--muted);margin:0 6px;font-weight:400}.source-chip{padding:2px 8px;background:var(--td-bg-color-secondarycontainer,#f3f3f3);color:var(--muted)}.source-model{color:var(--blue);background:var(--td-brand-color-light)}.fixture-score{display:flex;flex-direction:column;gap:4px}.fixture-score strong{font-size:24px}
.daily-comparison-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr))}.market-comparison{min-width:0;padding:16px 20px;border-right:1px solid var(--border);background:#fff}.market-comparison:last-child{border-right:0}.market-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.market-heading h4{font-size:14px;margin:0;line-height:24px}.market-heading small{font-weight:400}.result-badge{font-size:12px;line-height:24px;color:var(--muted)}.is-hit .result-badge{color:var(--hit)}.is-miss .result-badge{color:var(--miss)}
.prediction-value{display:grid;gap:8px;margin:16px 0}.prediction-value strong{font-size:16px;color:var(--blue);line-height:1.5}.prediction-value .no-selection{font-size:14px;color:var(--muted);font-weight:400}.actual-value{display:flex;align-items:baseline;justify-content:space-between;gap:8px;padding-top:12px;border-top:1px solid var(--border)}.actual-value strong{font-size:14px;text-align:right}.probability-note{margin:12px 0 0;font-size:12px;line-height:1.6;color:var(--muted)}.probability-note b{font-weight:500;color:var(--td-text-color-primary,#1f2329)}
.odds-note{display:grid;gap:4px;font-size:12px;line-height:1.6;color:var(--muted);margin:0 0 12px}.odds-note b{font-weight:400;color:var(--td-text-color-primary,#1f2329);font-variant-numeric:tabular-nums}.odds-note summary,.exploration summary{cursor:pointer}.exploration{border-top:1px solid var(--border);margin-top:16px;padding-top:12px;display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:12px}.exploration strong{font-size:14px;font-weight:500}
.daily-pagination{margin-top:20px}.daily-version{border-top:1px solid var(--border);margin-top:20px;padding-top:8px}.daily-alert{padding:16px;color:var(--miss)}.audit-rules{display:grid;gap:16px;line-height:1.8}:deep(.t-drawer){max-width:100vw;overflow-wrap:anywhere}.share-preview{min-width:0}.share-preview img{display:block;width:100%;height:auto;margin-top:16px;border:1px solid var(--border)}.share-preview .share-actions{margin-top:16px}
@media(max-width:1200px){.daily-rate{padding:16px}.market-comparison{padding:16px 12px}.daily-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:800px){.daily-rate-grid,.daily-comparison-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.daily-rate,.market-comparison{border-bottom:1px solid var(--border)}.daily-rate:nth-child(2n),.market-comparison:nth-child(2n){border-right:0}.daily-rate:last-child,.market-comparison:last-child{grid-column:1/-1;border-bottom:0}.daily-rate:last-child{max-width:none}}
@media(max-width:600px){.daily-filters{padding:16px;gap:12px;margin-bottom:16px}.daily-filters label:nth-child(4){grid-column:1/-1}.daily-filters :deep(.t-date-picker),.daily-filters :deep(.t-select){width:100%;min-width:0}.daily-summary-heading,.summary-switch,.paired-heading{padding:16px}.daily-summary{margin-bottom:16px}.daily-match-list{gap:16px}.daily-match-heading{padding:16px;gap:12px}.fixture-info{flex-basis:100%;min-width:0}.fixture-score{flex:1}.fixture-info h3{font-size:16px}.market-comparison{padding:16px 12px}.actual-value{flex-wrap:wrap}.daily-pagination :deep(.t-pagination){flex-wrap:wrap;gap:12px}.daily-workspace :deep(.t-button){min-height:40px}.odds-note summary{min-height:32px;display:flex;align-items:center}.share-actions{gap:8px}}
</style>

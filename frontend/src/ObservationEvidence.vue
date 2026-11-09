<script setup>
import { computed } from 'vue';
import { beijingDateTime } from './api';
import { researchLabel } from './researchView';
const props=defineProps({summary:Object});
const groups=computed(()=>[{key:'personnel',label:'人员事实'},{key:'workload',label:'比赛负荷'}]);
function position(value){const name=typeof value==='object'&&value?(value.displayName||value.name||value.abbreviation):value;return {G:'门将',GK:'门将',Goalkeeper:'门将',D:'后卫',DF:'后卫',Defender:'后卫',M:'中场',MF:'中场',Midfielder:'中场',F:'前锋',FW:'前锋',Forward:'前锋'}[name]||name||'位置未提供';}
const text=row=>row.workload?`${row.side==='home'?'主队':row.side==='away'?'客队':'球队'} · ${row.workload.observed_club_matches_30d!=null?`全赛事球队赛程 · 近14天${row.workload.observed_club_matches_14d??'—'}场 / 30天${row.workload.observed_club_matches_30d}场`:`最近5场来源 · 已观察${row.workload.observed_match_count??'—'}场 · 近14天至少${row.workload.matches_previous_14_days_lower_bound??'—'}场`}`:[row.name,researchLabel(row.status)].filter(Boolean).join(' · ')||row.text||row.summary||row.label||'事实记录';
</script>
<template>
  <section v-if="summary" class="panel research-evidence" aria-label="最新采集事实与负荷">
    <div class="panel-heading"><h2>最新采集事实与负荷</h2><span v-if="summary.captured_at" class="muted small">{{beijingDateTime(summary.captured_at)}}</span></div>
    <div class="research-content">
      <div v-if="summary.rosters?.length" class="fact-group"><h3>来源球队名单</h3><details v-for="(roster,index) in summary.rosters" :key="roster.snapshot_id||index"><summary>{{roster.side==='home'?'主队':roster.side==='away'?'客队':'球队'}} · {{roster.listed_count ?? '—'}} 人<span v-if="Object.keys(roster.concurrent_listing_conflicts||{}).length" class="roster-conflict"> · 名单存在来源冲突</span></summary><dl><dt>名单来源</dt><dd><a v-if="roster.source_url" :href="roster.source_url" target="_blank" rel="noopener noreferrer">查看来源</a><span v-else>未提供</span></dd><dt>采集时间</dt><dd>{{beijingDateTime(roster.captured_at)}}</dd><dt>有效期至</dt><dd>{{roster.valid_until?beijingDateTime(roster.valid_until):'未提供'}}</dd><dt>注册及资格</dt><dd>待核查</dd></dl><ul class="roster-players"><li v-for="(player,i) in roster.players||[]" :key="player.player_id||i"><span>{{player.name||'姓名未提供'}}</span><small>{{position(player.position)}}</small></li></ul></details></div>
      <div v-for="group in groups" :key="group.key" class="fact-group"><h3>{{group.label}} <small>{{summary[group.key]?.length||0}}</small></h3><p v-if="!summary[group.key]?.length" class="muted">待核实</p><details v-for="(row,index) in summary[group.key]||[]" :key="row.id||index"><summary>{{text(row)}}</summary><dl><dt>来源</dt><dd><a v-if="row.source_url" :href="row.source_url" target="_blank" rel="noopener noreferrer">{{row.source||'查看来源'}}</a><span v-else>{{row.source||'未记录'}}</span></dd><dt>采集时间</dt><dd>{{beijingDateTime(row.captured_at||row.recorded_at)}}</dd><dt>发布时间</dt><dd>{{row.published_at?beijingDateTime(row.published_at):'未提供'}}</dd><dt>不确定性</dt><dd>{{researchLabel(row.uncertainty)||(row.workload?row.text:null)||'未标注'}}</dd></dl><pre v-if="row.workload||row.coverage||row.players">{{JSON.stringify(row.workload||row.coverage||row.players,null,2)}}</pre></details></div>
      <details v-if="summary.unknown?.length"><summary>待核实 · {{summary.unknown.length}}</summary><p v-for="(item,index) in summary.unknown" :key="index">{{typeof item==='string'?researchLabel(item):item.message||item.reason||researchLabel(item.kind)}}</p></details>
      <details v-if="summary.alerts?.length"><summary>变化核查 · {{summary.alerts.length}}</summary><p v-for="(item,index) in summary.alerts" :key="index">{{item.message||item.label||researchLabel(item.kind)}}</p></details>
    </div>
  </section>
</template>
<style scoped>
.roster-conflict{color:#ad6800}.roster-players{list-style:none;margin:0;padding:0}.roster-players li{display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-top:1px solid #e7e9ed;overflow-wrap:anywhere}.roster-players li span{min-width:0}.roster-players small{flex-shrink:0;color:#536579}
.research-content{padding:0 24px 20px}.fact-group{padding:16px 0;border-top:1px solid #e7e9ed}.fact-group h3{font-size:15px;margin:0 0 12px}.fact-group small{color:#536579;font-weight:400}.research-evidence details{padding:12px 0;border-top:1px solid #e7e9ed}.research-evidence summary{cursor:pointer;color:#0052d9;overflow-wrap:anywhere}.research-evidence dl{display:grid;grid-template-columns:85px minmax(0,1fr);gap:10px;color:#536579}.research-evidence dd{margin:0;overflow-wrap:anywhere}.research-evidence pre{font:12px/1.7 monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f8;padding:12px}@media(max-width:700px){.research-content{padding:0 16px 16px}.research-evidence .panel-heading{flex-wrap:wrap;gap:8px}}
</style>

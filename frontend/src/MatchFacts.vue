<script setup>
import { timeLabel } from './api';
defineProps({ data: Object });
</script>
<template>
  <section v-if="data && (data.statistics?.length || data.events?.length)" class="panel mt16 match-facts" aria-label="比赛过程资料">
    <div class="panel-heading"><h2>比赛统计与关键事件</h2><a :href="data.source_url" target="_blank" rel="noopener noreferrer">{{ data.source }}来源</a></div>
    <p class="section-note">获取于 {{ timeLabel(data.captured_at) }} · 比赛累计统计，可能包含加时；关键事件不代表完整逐秒记录。</p>
    <t-table v-if="data.statistics?.length" row-key="code" :data="data.statistics" :columns="[
      {colKey:'label',title:'统计',minWidth:160}, {colKey:'home',title:'主队',width:130}, {colKey:'away',title:'客队',width:130}
    ]" size="small"><template #home="{row}">{{ row.home ?? '—' }}</template><template #away="{row}">{{ row.away ?? '—' }}</template></t-table>
    <t-table v-if="data.events?.length" row-key="id" :data="data.events" :columns="[
      {colKey:'clock',title:'时间',width:80}, {colKey:'period_label',title:'时段',width:100}, {colKey:'team',title:'球队',width:130},
      {colKey:'label',title:'事件',width:100}, {colKey:'participants',title:'相关球员（来源姓名）',minWidth:240,ellipsis:true}
    ]" size="small" class="mt16" />
  </section>
</template>
<style scoped>.match-facts :deep(td),.match-facts :deep(th) {white-space:nowrap;}</style>

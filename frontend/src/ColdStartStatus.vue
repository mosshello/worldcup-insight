<script setup>
import { computed, ref } from 'vue';
const props = defineProps({ model: Object, home: String, away: String });
const open = ref(false);
const active = computed(() => props.model?.cold_start_policy?.passed === true
  && props.model?.cold_start_policy?.policy === 'gamma_cold_start_v1'
  && Array.isArray(props.model?.team_samples) && props.model.team_samples.length === 2
  && props.model.team_samples.every(n => Number.isInteger(n) && n >= 0)
  && props.model.team_samples.some(n => n < 5));
const intervals = computed(() => {
  const u = props.model?.uncertainty;
  if (u?.interpretation !== 'parameter_uncertainty_not_match_score_interval'
      || !Array.isArray(u.goal_rate_intervals) || u.goal_rate_intervals.length !== 2) return [];
  return u.goal_rate_intervals.map((range, i) => ({ team: [props.home, props.away][i],
    samples: props.model.team_samples[i], interval: Array.isArray(range) && range.length === 2
      && range.every(v => Number.isFinite(v) && v >= 0) && range[0] <= range[1]
      ? range.map(v => v.toFixed(2)).join(' – ') : '未取得' }));
});
</script>
<template>
  <div v-if="active" class="cold-start-status" data-testid="cold-start-status">
    <t-tag theme="warning" variant="light">低样本</t-tag>
    <span>主 {{ model.team_samples[0] }} / 客 {{ model.team_samples[1] }} 场</span>
    <t-button v-if="intervals.length" variant="text" @click="open = true">参数区间</t-button>
    <t-drawer v-model:visible="open" header="预期进球参数区间" size="560px" :footer="false">
      <t-table row-key="team" :data="intervals" :columns="[
        { colKey: 'team', title: '球队', ellipsis: true },
        { colKey: 'samples', title: '同赛事历史', width: 110 },
        { colKey: 'interval', title: `${Math.round(model.uncertainty.level * 100)}% 参数区间`, width: 180 },
      ]" />
    </t-drawer>
  </div>
</template>
<style scoped>
.cold-start-status { display: flex; align-items: center; gap: 12px; margin: 12px 0; font-size: 13px; }
.cold-start-status :deep(td), .cold-start-status :deep(th) { white-space: nowrap; }
</style>

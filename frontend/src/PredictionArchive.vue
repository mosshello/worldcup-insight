<script setup>
import { computed, ref, watch } from 'vue';
import { beijingDateTime } from './api';
const props = defineProps({ versions: { type: Array, default: () => [] }, matchId: String, compact: Boolean, initialVersion: {type: String, default: ''} });
const selectedId = ref('');
const records = computed(() => [...props.versions].sort((a, b) => Number(b.version) - Number(a.version)));
watch(() => [props.matchId, props.initialVersion], () => { selectedId.value = props.initialVersion; }, {immediate:true});
const current = computed(() => records.value.find(v => String(v.id ?? v.version) === selectedId.value) || records.value[0]);
const options = computed(() => records.value.map(v => ({
  value: String(v.id ?? v.version), label: `v${v.version} · ${beijingDateTime(v.created)}`,
})));
const groups = computed(() => {
  const v = current.value || {};
  return [
    { name: '足球模型归档', rows: v.football_model?.markets || [] },
    { name: '市场参考归档', rows: v.market_reference?.market_assessments || (v.schema_version === 2 ? [] : v.market_assessments) || [] },
  ].filter(g => g.rows.length);
});
const probability = v => v == null || !Number.isFinite(Number(v)) ? '未记录' : `${Number(v).toFixed(1)}%`;
</script>
<template>
  <section v-if="current" class="prediction-archive mt16" :class="{compact}" aria-label="当时预测">
    <div class="panel-heading flush">
      <h3 v-if="!compact">当时预测 · 只读</h3>
      <t-select :value="String(current.id ?? current.version)" :options="options"
        :on-change="value => selectedId = String(value)" aria-label="预测归档版本" style="width: 250px; max-width: 100%" />
    </div>
    <p class="small muted">保存时间（北京）：{{ beijingDateTime(current.created) }}</p>
    <p>{{ current.title || current.direction || '方向未记录' }}</p>
    <div class="archive-probabilities">
      <div v-for="(label, index) in ['主胜', '平局', '客胜']" :key="label">
        <span>{{ label }}</span><strong>{{ probability(current.probabilities?.[index]) }}</strong>
      </div>
    </div>
    <p class="small muted">{{ current.schema_version === 2 ? '足球模型概率' : (current.probLabel || '原记录概率') }} · {{ current.model_version || '模型版本未记录' }}</p>
    <p v-if="current.scores || current.goals">比分：{{ current.scores || '未记录' }} · 总进球：{{ current.goals || '未记录' }}</p>
    <section v-for="group in groups" :key="group.name" class="mt16">
      <h4>{{ group.name }}</h4>
      <t-table v-if="group.rows.some(row => row.selection != null)" row-key="code" :data="group.rows.filter(row => row.selection != null)" size="small" :columns="[
        { colKey: 'label', title: '玩法', width: 100 },
        { colKey: 'selection', title: '当时选择', minWidth: 170 },
        { colKey: 'coverage', title: '选择概率', width: 100 },
      ]"><template #coverage="{ row }">{{ probability(row.coverage) }}</template></t-table>
      <details v-for="market in group.rows.filter(row => row.options?.length)" :key="market.code" class="mt8">
        <summary>{{ market.label }} · 各选项概率</summary>
        <t-table row-key="key" :data="market.options" size="small" :columns="[
          { colKey: 'label', title: '选项', minWidth: 180 },
          { colKey: 'probability', title: '归档概率', width: 100 },
          { colKey: 'odds', title: '当时报价', width: 100 },
        ]">
          <template #probability="{row}">{{ probability(row.probability) }}</template>
          <template #odds="{row}">{{ row.odds ?? '未记录' }}</template>
        </t-table>
      </details>
    </section>
    <details v-if="current.evidence?.length || current.risks?.length || current.narrative" class="mt16" :open="!compact">
      <summary>预测依据与风险</summary>
      <p v-if="current.evidence?.length" class="review-text">{{ current.evidence.map(e => e.text).filter(Boolean).join('\n') }}</p>
      <p v-if="current.risks?.length" class="review-text">{{ current.risks.join('\n') }}</p>
      <p v-if="current.narrative" class="review-text">{{ current.narrative }}</p>
    </details>
  </section>
</template>
<style scoped>
.prediction-archive { border: 1px solid var(--td-component-border); padding: 16px; }
.prediction-archive.compact { border: 0; padding: 0; }
.prediction-archive.compact :deep(.t-table td), .prediction-archive.compact :deep(.t-table th) { padding: 6px 10px; height: auto; }
.prediction-archive.compact .archive-probabilities { margin-top: 8px; }
.prediction-archive .panel-heading { flex-wrap: wrap; gap: 12px; }
.archive-probabilities { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }
.archive-probabilities span, .archive-probabilities strong { display: block; }
.archive-probabilities strong { margin-top: 4px; font-size: 20px; }
.prediction-archive :deep(.t-table td) { white-space: nowrap; }
</style>

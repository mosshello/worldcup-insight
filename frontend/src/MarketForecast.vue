<script setup>
import { ref } from "vue";
defineProps({ rows: { type: Array, default: () => [] } });
const selected = ref(null),
  open = ref(false);
const percent = (n) => (Number.isFinite(n) ? `${n.toFixed(1)}%` : "—");
function inspect(row) {
  selected.value = row;
  open.value = true;
}
</script>
<template>
  <t-table
    row-key="code"
    :data="rows"
    size="small"
    :columns="[
      { colKey: 'label', title: '玩法', width: 120 },
      { colKey: 'line', title: '让球', width: 80 },
      { colKey: 'selection', title: '观察选项', minWidth: 180 },
      { colKey: 'selection_count', title: '选项数', width: 90 },
      { colKey: 'coverage', title: '概率合计', width: 100 },
      { colKey: 'action', title: '详情', width: 100 },
    ]"
  >
    <template #line="{ row }">{{
      row.line == null ? "—" : (row.line > 0 ? "+" : "") + row.line
    }}</template>
    <template #selection="{ row }">{{
      row.selection || "未归档选择"
    }}</template>
    <template #selection_count="{ row }">{{
      row.selection_count ?? "—"
    }}</template>
    <template #coverage="{ row }">{{
      row.selection_count ? percent(row.coverage) : "—"
    }}</template>
    <template #action="{ row }"
      ><t-button
        variant="text"
        :disabled="!row.options?.length"
        @click="inspect(row)"
        >查看概率</t-button
      ></template
    >
  </t-table>
  <t-drawer
    v-model:visible="open"
    :header="selected?.label || '玩法概率'"
    size="min(640px, 100vw)"
    :footer="false"
  >
    <template v-if="selected">
      <p class="muted">
        {{
          selected.origin === "model" ? "模型概率 · 前瞻观察" : "市场参考概率"
        }}
      </p>
      <t-table
        row-key="key"
        :data="selected.options"
        size="small"
        :columns="[
          { colKey: 'label', title: '选项', minWidth: 100 },
          { colKey: 'odds', title: 'SP', width: 80 },
          { colKey: 'probability', title: '概率', width: 90 },
          ...(selected.origin === 'model'
            ? [
                { colKey: 'market_probability', title: '市场概率', width: 100 },
                { colKey: 'difference', title: '差值', width: 90 },
              ]
            : []),
        ]"
      >
        <template #odds="{ row }">{{
          row.odds == null ? "—" : row.odds.toFixed(2)
        }}</template>
        <template #probability="{ row }">{{
          percent(row.probability)
        }}</template>
        <template #market_probability="{ row }">{{
          percent(row.market_probability)
        }}</template>
        <template #difference="{ row }">{{
          Number.isFinite(row.difference)
            ? `${row.difference > 0 ? "+" : ""}${row.difference.toFixed(1)}pp`
            : "—"
        }}</template>
      </t-table>
      <p class="small muted">{{ selected.reason || "历史归档" }}</p>
    </template>
  </t-drawer>
</template>

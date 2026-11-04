<script setup>
import { statusNames, shortVersion } from "./view-model";
import { timeLabel } from "../api";
defineProps({
  rows: { type: Array, default: () => [] },
  columns: { type: Array, required: true },
  loading: Boolean,
});
defineEmits(["detail"]);
function cell(row, key) {
  const value = row[key];
  if (value == null || value === "") return "—";
  if (
    ["digest", "dataset_digest", "run_id"].includes(key) ||
    (key === "id" && String(value).length > 16)
  )
    return shortVersion(value);
  if (key.endsWith("_at") || key === 'kickoff') return timeLabel(value);
  if (key.endsWith("status") || key === "kind")
    return statusNames[value] || value;
  return Array.isArray(value)
    ? value.join(" / ")
    : typeof value === "object"
      ? "查看详情"
      : value;
}
</script>
<template>
  <t-table
    :data="
      rows.map((r, i) => ({
        ...r,
        _rowKey: `${r.id || r.digest || i}:${r.season || ''}`,
      }))
    "
    :columns="
      columns.map(([colKey, title, width]) => ({
        colKey,
        title,
        width: width || 160,
        ellipsis: true,
      }))
    "
    row-key="_rowKey"
    :pagination="
      rows.length > 10
        ? { defaultPageSize: 10, total: rows.length, showJumper: true }
        : undefined
    "
    :loading="loading"
    empty="暂无记录"
    table-layout="fixed"
  >
    <template v-for="[key] in columns" :key="key" #[key]="{ row }">
      <t-button
        v-if="key === 'action'"
        variant="text"
        theme="primary"
        @click="$emit('detail', row)"
        >详情</t-button
      >
      <slot v-else :name="key" :row="row">{{ cell(row, key) }}</slot>
    </template>
  </t-table>
</template>

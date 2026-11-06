<script setup>
import { statusNames, shortVersion } from "./view-model";
import { timeLabel } from "../api";
import { computed, ref, watch } from 'vue';
import { usePhoneLayout } from '../usePhoneLayout';
const phone = usePhoneLayout();
const page = ref(1);
const props = defineProps({
  rows: { type: Array, default: () => [] },
  columns: { type: Array, required: true },
  loading: Boolean,
});
defineEmits(["detail"]);
const pageRows = computed(() => props.rows.slice((page.value - 1) * 10, page.value * 10));
watch(() => props.rows, () => { page.value = 1; });
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
  <section v-if="phone" class="phone-records">
    <article v-for="(row, index) in pageRows" :key="row.id || row.digest || index" class="phone-record">
      <dl><template v-for="[key, title] in columns" :key="key">
        <template v-if="key !== 'action'"><dt>{{ title }}</dt><dd><slot :name="key" :row="row">{{ cell(row, key) }}</slot></dd></template>
      </template></dl>
      <t-button v-if="columns.some(([key]) => key === 'action')" variant="text" theme="primary" @click="$emit('detail', row)">详情</t-button>
    </article>
    <p v-if="!rows.length" class="empty-state">{{ loading ? '加载中' : '暂无记录' }}</p>
    <t-pagination v-if="rows.length > 10" v-model="page" :total="rows.length" :page-size="10" :show-page-size="false" :max-page-btn="3" size="small" />
  </section>
  <t-table v-else
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

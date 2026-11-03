<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from "vue";
import { timeLabel } from "./api";
const props = defineProps({ value: Object, large: Boolean, compact: Boolean });
const now = ref(Date.now());
let timer;
onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 10000);
});
onBeforeUnmount(() => clearInterval(timer));
const delayed = computed(
  () =>
    props.value?.stale ||
    (props.value?.state !== "completed" &&
      now.value - Date.parse(props.value?.observed_at) > 90000),
);
const time = computed(() =>
  props.value?.observed_at
    ? new Date(props.value.observed_at).toLocaleTimeString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "",
);
const label = computed(() =>
  delayed.value && props.value?.observed_at
    ? "更新延迟"
    : props.value?.label || "待更新",
);
</script>
<template>
  <div
    class="live-score"
    :class="{
      large,
      compact,
      delayed,
      playing: value?.state === 'live' && !delayed,
    }"
    :title="`${value?.source || ''} ${value?.observed_at ? '核验 ' + timeLabel(value.observed_at) : ''} ${value?.error || ''}`"
  >
    <strong class="live-score-value mono">{{
      value?.score?.replace("-", " – ") || "—"
    }}</strong>
    <span class="live-score-phase"
      >{{ label
      }}<template v-if="!delayed && value?.clock">
        · {{ value.clock }}</template
      ></span
    >
    <span v-if="time && !compact" class="live-score-time">核验 {{ time }}</span>
    <a
      v-if="large && value?.source_url"
      :href="value.source_url"
      target="_blank"
      rel="noopener noreferrer"
      class="live-score-source"
      >{{ value.source }} ↗</a
    >
  </div>
</template>

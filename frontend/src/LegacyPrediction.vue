<script setup>
import { timeLabel } from "./api";
defineProps({ record: Object });
</script>
<template>
  <section v-if="record" class="panel mt16" aria-label="旧版预测记录">
    <div class="panel-heading">
      <h3>旧版预测记录 · 只读</h3>
      <t-tag variant="light">来源：旧版预测日志</t-tag>
    </div>
    <p class="section-note">
      初始方向：<strong>{{ record.direction }}</strong> · 比分：{{
        record.score || "未记录"
      }}
    </p>
    <p
      v-if="
        record.latest_direction && record.latest_direction !== record.direction
      "
      class="section-note"
    >
      日志末次方向：{{ record.latest_direction }}
    </p>
    <p class="section-note">
      日志记录时间：{{ timeLabel(record.recorded_at) }} ·
      {{ record.recorded_before_kickoff ? "早于开球" : "晚于开球" }}
    </p>
    <p class="section-note">{{ record.notice }}</p>
  </section>
</template>

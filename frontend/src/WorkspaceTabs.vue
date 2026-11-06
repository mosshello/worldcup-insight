<script setup>
import { usePhoneLayout } from './usePhoneLayout';
defineProps({ tabs: Array, value: String, label: String });
defineEmits(['change']);
const phone = usePhoneLayout();
</script>
<template>
  <nav v-if="phone" class="phone-tabs" :aria-label="label" :style="{ '--columns': tabs.length > 4 ? 3 : 4 }">
    <button v-for="[key, title] in tabs" :key="key" :aria-current="key === value ? 'page' : undefined"
      @click="$emit('change', key)">{{ title }}</button>
  </nav>
  <t-tabs v-else :value="value" class="detail-tabs" @change="$emit('change', $event)">
    <t-tab-panel v-for="[key, title] in tabs" :key="key" :value="key" :label="title" />
  </t-tabs>
</template>

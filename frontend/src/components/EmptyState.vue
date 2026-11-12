<script setup>
import { Inbox, LoaderCircle } from "@lucide/vue";
defineProps({
  title: { type: String, default: "正在加载页面…" },
  description: String,
  loading: Boolean,
  icon: { type: [Object, Function], default: () => Inbox },
});
</script>
<template>
  <div class="empty-state" :aria-busy="loading">
    <span class="empty-state-icon"
      ><LoaderCircle
        v-if="loading"
        :size="24"
        class="spin"
        aria-hidden="true" /><component
        v-else
        :is="icon"
        :size="24"
        aria-hidden="true" /></span
    ><strong>{{ title }}</strong>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.default" class="empty-state-actions"><slot /></div>
  </div>
</template>

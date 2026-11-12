<script setup>
import { Clock3, RefreshCw } from "@lucide/vue";
import { dateTime } from "../lib/format";
defineProps({ updatedAt: Number, loading: Boolean, label: String });
const time = (value) =>
  new Date(value).toLocaleTimeString("zh-CN", {
    hour12: false,
    timeZone: "Asia/Shanghai",
  });
</script>
<template>
  <div class="resource-meta">
    <span v-if="label">{{ label }}</span
    ><span
      v-if="updatedAt || loading"
      :title="updatedAt ? dateTime(updatedAt) : undefined"
      ><RefreshCw
        v-if="loading"
        :size="14"
        class="spin"
        aria-hidden="true"
      /><Clock3 v-else :size="14" aria-hidden="true" />{{
        loading ? "正在更新" : `更新于 ${time(updatedAt)}`
      }}</span
    >
  </div>
</template>

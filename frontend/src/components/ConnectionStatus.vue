<script setup>
import { useResource } from "../composables/useResource";
import { consoleApi } from "../services/console";
const { data, error, updatedAt } = useResource(consoleApi.status, {}, 15000);
</script>
<template>
  <div class="connection-status" :class="{ disconnected: error }" role="status">
    <span class="status-dot-label"
      ><i />{{
        error ? "服务连接异常" : updatedAt ? "服务已连接" : "正在连接"
      }}</span
    >
    <small v-if="data.version" :title="data.revision || '未提供构建提交号'"
      >v{{ data.version
      }}{{ data.revision ? ` · ${data.revision.slice(0, 7)}` : "" }}</small
    >
  </div>
</template>

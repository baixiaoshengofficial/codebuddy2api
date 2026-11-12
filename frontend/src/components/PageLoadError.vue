<script setup>
import { TriangleAlert } from "@lucide/vue";
import EmptyState from "./EmptyState.vue";
const reload = () => window.location.reload();
</script>
<template>
  <EmptyState
    :icon="TriangleAlert"
    title="页面未能加载"
    description="可能是网络中断或服务刚刚更新，请刷新页面后再试。"
    ><button class="button primary" @click="reload">
      刷新页面
    </button></EmptyState
  >
</template>

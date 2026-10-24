<script setup>
defineProps({ error: String });
defineEmits(["retry"]);
</script>
<template>
  <div v-if="error" class="notice error" role="alert">
    <span>数据更新失败：{{ error }}。已显示的数据可能过期。</span
    ><button class="text-button" @click="$emit('retry')">重试</button>
  </div>
</template>

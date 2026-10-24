<script setup>
defineProps({ title: String, description: String, eyebrow: String });
</script>
<template>
  <div class="view-toolbar">
    <div>
      <p v-if="eyebrow" class="eyebrow">{{ eyebrow }}</p>
      <h2>{{ title }}</h2>
      <p v-if="description" class="page-description">{{ description }}</p>
    </div>
    <div class="toolbar-actions"><slot /></div>
  </div>
</template>

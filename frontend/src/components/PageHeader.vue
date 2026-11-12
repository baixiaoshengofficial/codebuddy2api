<script setup>
defineProps({ title: String, description: String, eyebrow: String });
</script>
<template>
  <header class="view-toolbar">
    <div>
      <p v-if="eyebrow" class="eyebrow">{{ eyebrow }}</p>
      <h1 tabindex="-1">{{ title }}</h1>
      <p v-if="description" class="page-description">{{ description }}</p>
    </div>
    <div v-if="$slots.default" class="toolbar-actions"><slot /></div>
  </header>
</template>

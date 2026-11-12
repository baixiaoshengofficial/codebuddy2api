<script setup>
defineProps({
  label: String,
  value: [Number, String],
  hint: String,
  icon: [Object, Function],
});
</script>
<template>
  <article class="metric-card">
    <div class="metric-heading">
      <span>{{ label }}</span
      ><span v-if="icon" class="metric-icon"
        ><component :is="icon" :size="18" aria-hidden="true"
      /></span>
    </div>
    <strong>{{ value }}</strong
    ><small>{{ hint }}</small>
  </article>
</template>

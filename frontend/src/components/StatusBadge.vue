<script setup>
import { stateLabels } from "../lib/format";
defineProps({ state: String, label: String });
</script>
<template>
  <span class="status-badge" :class="state"
    ><i />{{ label || stateLabels[state] || state }}</span
  >
</template>

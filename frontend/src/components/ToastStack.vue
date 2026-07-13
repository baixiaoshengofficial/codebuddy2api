<script setup>
import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from '@lucide/vue'
import { dismiss, notifications } from '../lib/notify'

const icons = {
  success: CheckCircle2,
  error: AlertCircle,
  warning: TriangleAlert,
  info: Info,
}
</script>

<template>
  <div class="toast-stack" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="item in notifications" :key="item.id" class="toast" :class="`toast-${item.type}`">
        <component :is="icons[item.type] || Info" :size="18" />
        <span>{{ item.message }}</span>
        <button class="icon-button compact" title="关闭" @click="dismiss(item.id)">
          <X :size="16" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

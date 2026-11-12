<script setup>
import { ref, useId } from "vue";
import { X } from "@lucide/vue";
import { useDialog } from "../composables/useDialog";
defineProps({
  title: String,
  subtitle: String,
  eyebrow: { type: String, default: "诊断详情" },
});
const emit = defineEmits(["close"]);
const panel = ref(null),
  titleId = useId();
const { cancel, backdrop, keydown } = useDialog(panel, () => emit("close"));
</script>
<template>
  <Teleport to="body"
    ><dialog
      ref="panel"
      class="drawer"
      :aria-labelledby="titleId"
      @cancel="cancel"
      @keydown="keydown"
      @click="backdrop"
    >
      <header>
        <div>
          <p class="eyebrow">{{ eyebrow }}</p>
          <h2 :id="titleId">{{ title }}</h2>
          <small>{{ subtitle }}</small>
        </div>
        <button
          class="icon-button"
          aria-label="关闭详情"
          @click="$emit('close')"
        >
          <X :size="20" aria-hidden="true" />
        </button>
      </header>
      <div class="drawer-body"><slot /></div></dialog
  ></Teleport>
</template>

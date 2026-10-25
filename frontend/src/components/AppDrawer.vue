<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, useId } from "vue";
import { X } from "@lucide/vue";
defineProps({
  title: String,
  subtitle: String,
  eyebrow: { type: String, default: "诊断详情" },
});
const emit = defineEmits(["close"]);
const panel = ref(null),
  titleId = useId();
let previous, scroll;
const focusable = () => [
  ...panel.value.querySelectorAll(
    'button:not(:disabled), a[href], input:not(:disabled), select, textarea, [tabindex="0"]',
  ),
];
function keydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
  }
  if (event.key !== "Tab") return;
  const elements = focusable(),
    first = elements[0],
    last = elements.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
onMounted(async () => {
  previous = document.activeElement;
  scroll = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  await nextTick();
  focusable()[0]?.focus();
});
onBeforeUnmount(() => {
  document.body.style.overflow = scroll;
  previous?.focus();
});
</script>
<template>
  <Teleport to="body"
    ><div class="drawer-backdrop" @click.self="emit('close')">
      <section
        ref="panel"
        class="drawer"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        @keydown="keydown"
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
            @click="emit('close')"
          >
            <X :size="20" />
          </button>
        </header>
        <div class="drawer-body"><slot /></div>
      </section></div
  ></Teleport>
</template>

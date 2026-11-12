<script setup>
import { ref, useId } from "vue";
import { X } from "@lucide/vue";
import { useDialog } from "../../composables/useDialog";
import AppNavigation from "./AppNavigation.vue";
defineProps({ active: String });
const emit = defineEmits(["navigate", "logout", "close"]);
const panel = ref(null),
  titleId = useId();
const { cancel, backdrop, keydown } = useDialog(panel, () => emit("close"));
</script>
<template>
  <Teleport to="body"
    ><dialog
      ref="panel"
      class="mobile-navigation"
      :aria-labelledby="titleId"
      @cancel="cancel"
      @keydown="keydown"
      @click="backdrop"
    >
      <header>
        <span :id="titleId">导航菜单</span
        ><button
          class="icon-button"
          aria-label="关闭导航"
          @click="$emit('close')"
        >
          <X :size="20" aria-hidden="true" />
        </button>
      </header>
      <AppNavigation
        :active="active"
        @navigate="$emit('navigate', $event)"
        @logout="$emit('logout')"
      /></dialog
  ></Teleport>
</template>

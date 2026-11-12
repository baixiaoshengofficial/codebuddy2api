<script setup>
import { defineAsyncComponent, onMounted, ref } from "vue";
import { Activity } from "@lucide/vue";
import LoginView from "./components/LoginView.vue";
import ToastStack from "./components/ToastStack.vue";
import { apiFetch, getStoredPassword, setStoredPassword } from "./lib/api";
import { useTheme } from "./composables/useTheme";
const AppShell = defineAsyncComponent(
  () => import("./components/AppShell.vue"),
);
const authenticated = ref(false),
  restoring = ref(Boolean(getStoredPassword()));
const { theme, toggleTheme } = useTheme();
function logout() {
  setStoredPassword("");
  authenticated.value = false;
}
onMounted(async () => {
  if (!getStoredPassword()) {
    restoring.value = false;
    return;
  }
  try {
    await apiFetch("/codebuddy/v1/credentials");
    authenticated.value = true;
  } catch {
    setStoredPassword("");
  } finally {
    restoring.value = false;
  }
});
</script>
<template>
  <div v-if="restoring" class="app-loading" role="status">
    <Activity class="spin" :size="28" aria-hidden="true" /><span
      >正在连接控制台…</span
    >
  </div>
  <LoginView v-else-if="!authenticated" @authenticated="authenticated = true" />
  <AppShell
    v-else
    :theme="theme"
    @toggle-theme="toggleTheme"
    @logout="logout"
  />
  <ToastStack />
</template>

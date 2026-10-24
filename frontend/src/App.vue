<script setup>
import { computed, onMounted, provide, ref, watch } from "vue";
import {
  Activity,
  Beaker,
  KeyRound,
  ListChecks,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Sun,
} from "@lucide/vue";
import LoginView from "./components/LoginView.vue";
import ToastStack from "./components/ToastStack.vue";
import ConnectionStatus from "./components/ConnectionStatus.vue";
import DashboardView from "./views/DashboardView.vue";
import CredentialsView from "./views/CredentialsView.vue";
import RequestLogsView from "./views/RequestLogsView.vue";
import ApiTestView from "./views/ApiTestView.vue";
import SettingsView from "./views/SettingsView.vue";
import { apiFetch, getStoredPassword, setStoredPassword } from "./lib/api";

const authenticated = ref(false);
const restoring = ref(Boolean(getStoredPassword()));
const activeView = ref(
  localStorage.getItem("codebuddy2api.activeView") || "credentials",
);
const sidebarOpen = ref(window.innerWidth > 860);
const theme = ref(localStorage.getItem("codebuddy2api.theme") || "light");

const views = [
  {
    id: "dashboard",
    label: "运行概览",
    icon: Activity,
    component: DashboardView,
  },
  {
    id: "credentials",
    label: "凭证管理",
    icon: KeyRound,
    component: CredentialsView,
  },
  {
    id: "requests",
    label: "请求明细",
    icon: ListChecks,
    component: RequestLogsView,
  },
  { id: "api-test", label: "API 测试", icon: Beaker, component: ApiTestView },
  {
    id: "settings",
    label: "服务设置",
    icon: Settings,
    component: SettingsView,
  },
];

let navigationGuard = () => true;
provide("registerNavigationGuard", (guard) => {
  navigationGuard = guard;
  return () => {
    if (navigationGuard === guard) navigationGuard = () => true;
  };
});

const currentView = computed(
  () => views.find((item) => item.id === activeView.value) || views[0],
);

watch(
  theme,
  (value) => {
    document.documentElement.dataset.theme = value;
    localStorage.setItem("codebuddy2api.theme", value);
  },
  { immediate: true },
);

watch(activeView, (value) =>
  localStorage.setItem("codebuddy2api.activeView", value),
);

function selectView(id) {
  if (id !== activeView.value && !navigationGuard()) return;
  activeView.value = id;
  if (window.innerWidth <= 860) sidebarOpen.value = false;
}

function logout() {
  if (!navigationGuard()) return;
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
  <div v-if="restoring" class="app-loading">
    <Activity class="spin" :size="28" />
  </div>
  <LoginView v-else-if="!authenticated" @authenticated="authenticated = true" />
  <div v-else class="app-shell" :class="{ 'sidebar-collapsed': !sidebarOpen }">
    <aside class="sidebar">
      <div class="brand-row">
        <div class="brand-mark small"><Activity :size="19" /></div>
        <div class="brand-copy">
          <strong>CodeBuddy2API</strong>
          <span>CONTROL ROOM</span>
        </div>
      </div>

      <nav class="primary-nav" aria-label="主导航">
        <button
          v-for="item in views"
          :key="item.id"
          class="nav-item"
          :class="{ active: activeView === item.id }"
          :title="item.label"
          @click="selectView(item.id)"
        >
          <component :is="item.icon" :size="19" />
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="sidebar-footer">
        <button class="nav-item" title="退出登录" @click="logout">
          <LogOut :size="19" /><span>退出登录</span>
        </button>
      </div>
    </aside>

    <header class="topbar">
      <button
        class="icon-button"
        :title="sidebarOpen ? '收起导航' : '展开导航'"
        @click="sidebarOpen = !sidebarOpen"
      >
        <PanelLeftClose v-if="sidebarOpen" :size="20" />
        <PanelLeftOpen v-else :size="20" />
      </button>
      <div class="page-identity">
        <h1>{{ currentView.label }}</h1>
      </div>
      <ConnectionStatus />
      <button
        class="icon-button"
        :title="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
        @click="theme = theme === 'dark' ? 'light' : 'dark'"
      >
        <Sun v-if="theme === 'dark'" :size="20" />
        <Moon v-else :size="20" />
      </button>
    </header>

    <button
      v-if="sidebarOpen"
      class="mobile-nav-backdrop"
      aria-label="关闭导航"
      @click="sidebarOpen = false"
    />
    <main class="workspace">
      <component :is="currentView.component" />
    </main>
  </div>
  <ToastStack />
</template>

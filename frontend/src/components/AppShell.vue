<script setup>
import { computed, defineAsyncComponent, nextTick, ref, watch } from "vue";
import {
  ChevronRight,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Sun,
} from "@lucide/vue";
import AppNavigation from "./shell/AppNavigation.vue";
import MobileNavigation from "./shell/MobileNavigation.vue";
import ConnectionStatus from "./ConnectionStatus.vue";
import PageLoading from "./PageLoading.vue";
import PageLoadError from "./PageLoadError.vue";
import { navigation } from "../lib/navigation";
import { useConsoleNavigation } from "../composables/useConsoleNavigation";
import { useMediaQuery } from "../composables/useMediaQuery";
import { readPreference, writePreference } from "../lib/preferences";
defineProps({ theme: String });
const emit = defineEmits(["logout", "toggle-theme"]);
const { activeView, selectView, canLeave } = useConsoleNavigation();
const mobile = useMediaQuery("(max-width: 767px)");
const tablet = useMediaQuery("(min-width: 768px) and (max-width: 1023px)");
const menuOpen = ref(false),
  collapsed = ref(
    readPreference("navigationCollapsed", tablet.value ? "true" : "false") ===
      "true",
  ),
  main = ref(null);
const loaders = {
  dashboard: () => import("../views/DashboardView.vue"),
  credentials: () => import("../views/CredentialsView.vue"),
  requests: () => import("../views/RequestLogsView.vue"),
  "api-test": () => import("../views/ApiTestView.vue"),
  settings: () => import("../views/SettingsView.vue"),
};
const pages = Object.fromEntries(
  Object.entries(loaders).map(([id, loader]) => [
    id,
    defineAsyncComponent({
      loader,
      loadingComponent: PageLoading,
      errorComponent: PageLoadError,
      delay: 150,
    }),
  ]),
);
const current = computed(() =>
  navigation.find((item) => item.id === activeView.value),
);
function navigate(id) {
  if (selectView(id)) menuOpen.value = false;
}
function toggleNavigation() {
  if (mobile.value) menuOpen.value = true;
  else {
    collapsed.value = !collapsed.value;
    writePreference("navigationCollapsed", String(collapsed.value));
  }
}
function focusPage() {
  main.value?.querySelector("h1")?.focus();
}
function logout() {
  if (canLeave()) emit("logout");
}
watch(tablet, (value) => {
  collapsed.value = value;
});
watch(mobile, () => {
  menuOpen.value = false;
});
watch(activeView, async () => {
  menuOpen.value = false;
  await nextTick();
  main.value?.querySelector("h1")?.focus();
});
</script>
<template>
  <a class="skip-link" href="#main-content" @click.prevent="main?.focus()"
    >跳到主要内容</a
  >
  <div
    class="app-shell"
    :class="{ 'sidebar-collapsed': collapsed, 'is-mobile': mobile }"
  >
    <aside v-if="!mobile" class="sidebar">
      <AppNavigation
        :active="activeView"
        :collapsed="collapsed"
        @navigate="navigate"
        @logout="logout"
      />
    </aside>
    <header class="topbar">
      <div class="topbar-location">
        <button
          class="icon-button"
          :aria-label="
            mobile ? '展开导航' : collapsed ? '展开导航' : '收起导航'
          "
          :title="mobile ? '展开导航' : collapsed ? '展开导航' : '收起导航'"
          :aria-expanded="mobile ? menuOpen : !collapsed"
          @click="toggleNavigation"
        >
          <Menu v-if="mobile" :size="20" aria-hidden="true" /><PanelLeftOpen
            v-else-if="collapsed"
            :size="20"
            aria-hidden="true"
          /><PanelLeftClose v-else :size="20" aria-hidden="true" />
        </button>
        <p class="page-identity">
          <span>工作空间</span
          ><ChevronRight :size="14" aria-hidden="true" /><strong>{{
            current.label
          }}</strong>
        </p>
      </div>
      <div class="topbar-actions">
        <ConnectionStatus /><button
          class="icon-button theme-toggle"
          :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
          :title="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
          @click="$emit('toggle-theme')"
        >
          <Sun v-if="theme === 'dark'" :size="20" aria-hidden="true" /><Moon
            v-else
            :size="20"
            aria-hidden="true"
          />
        </button>
      </div>
    </header>
    <main id="main-content" ref="main" class="workspace" tabindex="-1">
      <component :is="pages[activeView]" @vue:mounted="focusPage" />
    </main>
    <MobileNavigation
      v-if="mobile && menuOpen"
      :active="activeView"
      @close="menuOpen = false"
      @navigate="navigate"
      @logout="logout"
    />
  </div>
</template>

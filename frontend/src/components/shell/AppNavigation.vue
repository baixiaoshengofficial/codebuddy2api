<script setup>
import {
  Activity,
  Beaker,
  KeyRound,
  ListChecks,
  LogOut,
  Settings,
} from "@lucide/vue";
import { navigation } from "../../lib/navigation";
defineProps({ active: String, collapsed: Boolean });
defineEmits(["navigate", "logout"]);
const icons = {
  dashboard: Activity,
  credentials: KeyRound,
  requests: ListChecks,
  "api-test": Beaker,
  settings: Settings,
};
</script>
<template>
  <div class="brand-row">
    <span class="brand-mark small"
      ><Activity :size="20" aria-hidden="true"
    /></span>
    <div class="brand-copy">
      <strong>CodeBuddy2API</strong><span>管理控制台</span>
    </div>
  </div>
  <p class="nav-caption" v-if="!collapsed">工作空间</p>
  <nav class="primary-nav" aria-label="主导航">
    <a
      v-for="item in navigation"
      :key="item.id"
      :href="`#${item.id}`"
      class="nav-item"
      :class="{ active: active === item.id }"
      :aria-current="active === item.id ? 'page' : undefined"
      :aria-label="item.label"
      :title="collapsed ? item.label : item.hint"
      @click.prevent="$emit('navigate', item.id)"
    >
      <component :is="icons[item.id]" :size="20" aria-hidden="true" /><span>{{
        item.label
      }}</span>
    </a>
  </nav>
  <div class="sidebar-footer">
    <p v-if="!collapsed" class="nav-caption">账号与系统</p>
    <button class="nav-item" aria-label="退出登录" @click="$emit('logout')">
      <LogOut :size="20" aria-hidden="true" /><span>退出登录</span>
    </button>
  </div>
</template>

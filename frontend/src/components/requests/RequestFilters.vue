<script setup>
import { RotateCcw, Search } from "@lucide/vue";
const props = defineProps({
  filters: Object,
  options: Object,
  activeCount: Number,
  pendingSearch: Boolean,
  pendingRange: Boolean,
  rangeError: String,
});
const emit = defineEmits(["change", "search", "range", "reset"]);
const fields = [
  { key: "model", label: "模型", source: "models" },
  { key: "client", label: "来源", source: "clients" },
  { key: "credential", label: "凭证", source: "credentials" },
  { key: "status", label: "状态" },
];
const statuses = [
  { value: "success", label: "成功" },
  { value: "error", label: "失败" },
  { value: "pending", label: "处理中" },
];
function choices(field) {
  if (field.key === "status") return statuses;
  const values = [...(props.options?.[field.source] || [])];
  const selected = props.filters[field.key];
  if (selected && !values.includes(selected)) values.unshift(selected);
  return values.map((value) => ({ value, label: value }));
}
function set(key, value) {
  emit("change", { [key]: value });
}
function searchInput(event) {
  set("search", event.target.value);
  if (!event.target.value) emit("search");
}
</script>
<template>
  <div class="request-filters">
    <div class="request-filter-header">
      <div class="request-period-toolbar">
        <div class="segmented-control" role="group" aria-label="时间范围">
          <button
            v-for="[value, label] in [
              ['today', '今天'],
              ['24h', '近 24 小时'],
              ['7d', '近 7 天'],
              ['custom', '自定义'],
            ]"
            :key="value"
            type="button"
            :class="{ active: filters.period === value }"
            :aria-pressed="filters.period === value"
            @click="set('period', value)"
          >
            {{ label }}
          </button>
        </div>
        <small class="nowrap">北京时间 UTC+8</small>
      </div>
      <form class="search-form" role="search" @submit.prevent="$emit('search')">
        <input
          :value="filters.search"
          aria-label="搜索请求 ID 或输入"
          :placeholder="
            pendingSearch ? '回车应用搜索' : '搜索请求 ID 或输入内容'
          "
          :title="
            pendingSearch ? '搜索尚未应用，按回车或点击搜索' : '按回车搜索'
          "
          type="search"
          maxlength="200"
          @input="searchInput"
        />
        <button
          class="button secondary"
          :class="{ 'search-pending': pendingSearch }"
          aria-label="搜索请求"
        >
          <Search :size="16" />搜索
        </button>
      </form>
    </div>
    <form
      v-if="filters.period === 'custom'"
      class="request-range"
      @submit.prevent="$emit('range')"
    >
      <label
        >开始时间<input
          :value="filters.start"
          type="datetime-local"
          aria-label="开始时间（北京时间）"
          required
          @input="set('start', $event.target.value)"
      /></label>
      <label
        >结束时间<input
          :value="filters.end"
          type="datetime-local"
          aria-label="结束时间（北京时间）"
          required
          @input="set('end', $event.target.value)"
      /></label>
      <button class="button secondary">应用时间范围</button>
      <p v-if="rangeError" class="form-error range-feedback" role="alert">
        {{ rangeError }}
      </p>
      <small v-else-if="pendingRange" class="range-feedback"
        >时间已修改，点击“应用时间范围”后生效。</small
      >
    </form>
    <div class="request-filter-selects">
      <select
        v-for="field in fields"
        :key="field.key"
        :value="filters[field.key]"
        :aria-label="`筛选${field.label}`"
        :title="filters[field.key] || `全部${field.label}`"
        @change="set(field.key, $event.target.value)"
      >
        <option value="">全部{{ field.label }}</option>
        <option
          v-for="item in choices(field)"
          :key="item.value"
          :value="item.value"
          :title="item.label"
        >
          {{ item.label }}
        </option>
      </select>
      <button
        class="button secondary reset-filters"
        :disabled="!activeCount && !pendingSearch && !pendingRange"
        @click="$emit('reset')"
      >
        <RotateCcw :size="15" />重置<span v-if="activeCount" class="count">{{
          activeCount
        }}</span>
      </button>
    </div>
  </div>
</template>

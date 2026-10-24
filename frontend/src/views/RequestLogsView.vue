<script setup>
import { ref } from "vue";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  RefreshCw,
  Search,
} from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import ResourceNotice from "../components/ResourceNotice.vue";
import StatusBadge from "../components/StatusBadge.vue";
import RequestDetails from "../components/requests/RequestDetails.vue";
import { useRequestLogs } from "../composables/useRequestLogs";
import { dateTime, latency } from "../lib/format";
const {
  data,
  loading,
  error,
  refresh,
  filters,
  page,
  apply,
  changePage,
  exporting,
  exportCsv,
} = useRequestLogs();
const selected = ref(null);
</script>
<template>
  <section class="view-stack">
    <PageHeader
      title="请求明细"
      eyebrow="REQUESTS"
      description="追踪调用来源、凭证切换和上游请求过程。"
      ><button class="button secondary" :disabled="loading" @click="refresh">
        <RefreshCw :size="16" :class="{ spin: loading }" />刷新</button
      ><button
        class="button secondary"
        :disabled="exporting"
        @click="exportCsv"
      >
        <Download :size="16" />导出 CSV
      </button></PageHeader
    >
    <ResourceNotice :error="error" @retry="refresh" />
    <section class="data-section">
      <div class="filter-bar">
        <div class="segmented-control" aria-label="时间范围">
          <button
            v-for="[value, label] in [
              ['today', '今天'],
              ['24h', '近 24 小时'],
              ['7d', '近 7 天'],
              ['custom', '自定义'],
            ]"
            :key="value"
            :class="{ active: filters.period === value }"
            @click="filters.period = value"
          >
            {{ label }}
          </button>
        </div>
        <small>北京时间 UTC+8</small><span class="grow" />
        <form class="search-form" @submit.prevent="apply">
          <input
            v-model="filters.search"
            aria-label="搜索请求 ID 或输入"
            placeholder="搜索请求 ID 或输入内容"
            type="search"
          /><button class="icon-button" aria-label="搜索请求">
            <Search :size="17" />
          </button>
        </form>
      </div>
      <form
        v-if="filters.period === 'custom'"
        class="filter-bar"
        @submit.prevent="apply"
      >
        <label
          >开始<input
            v-model="filters.start"
            type="datetime-local"
            required /></label
        ><label
          >结束<input
            v-model="filters.end"
            type="datetime-local"
            required /></label
        ><button class="button secondary">应用时间范围</button>
      </form>
      <div class="filter-bar">
        <select v-model="filters.model" aria-label="筛选模型">
          <option value="">全部模型</option>
          <option v-for="item in data.filters.models" :key="item">
            {{ item }}
          </option></select
        ><select v-model="filters.client" aria-label="筛选来源">
          <option value="">全部来源</option>
          <option v-for="item in data.filters.clients" :key="item">
            {{ item }}
          </option></select
        ><select v-model="filters.credential" aria-label="筛选凭证">
          <option value="">全部凭证</option>
          <option v-for="item in data.filters.credentials" :key="item">
            {{ item }}
          </option></select
        ><select v-model="filters.status" aria-label="筛选状态">
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="error">失败</option>
          <option value="pending">处理中</option>
        </select>
      </div>
      <div class="table-shell">
        <table class="data-table request-table">
          <thead>
            <tr>
              <th>时间 / 来源</th>
              <th>状态</th>
              <th>模型 / 凭证</th>
              <th>上游尝试</th>
              <th>Tokens / 耗时</th>
              <th>本轮输入</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!data.items.length">
              <td colspan="7">
                <div class="empty-state">
                  {{ loading ? "正在加载…" : "当前范围没有请求" }}
                </div>
              </td>
            </tr>
            <tr v-for="item in data.items" :key="item.id">
              <td data-label="时间 / 来源">
                <strong>{{ dateTime(item.created_at) }}</strong
                ><small>{{ item.client }}</small>
              </td>
              <td data-label="状态"><StatusBadge :state="item.status" /></td>
              <td data-label="模型 / 凭证">
                <strong>{{ item.model }}</strong
                ><small>{{ item.credential || "—" }}</small>
              </td>
              <td data-label="上游尝试">
                <span
                  class="attempt-count"
                  :class="{ multiple: item.upstream_attempts > 1 }"
                  >{{ item.upstream_attempts }} 次</span
                >
              </td>
              <td data-label="Tokens / 耗时">
                <strong
                  >{{ item.token_estimated ? "~" : ""
                  }}{{ item.total_tokens ?? "—" }}</strong
                ><small>{{ latency(item.latency_ms) }}</small>
              </td>
              <td data-label="本轮输入" class="request-cell">
                <p class="truncate">{{ item.request_preview }}</p>
                <small class="truncate">ID {{ item.request_id }}</small>
              </td>
              <td>
                <button
                  class="text-button"
                  :aria-label="`查看请求 ${item.request_id}`"
                  @click="selected = item.id"
                >
                  查看详情
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <footer class="pagination">
        <span>共 {{ data.total }} 条 · 每 5 秒更新</span>
        <div>
          <button
            class="icon-button"
            aria-label="上一页"
            :disabled="page <= 1 || loading"
            @click="changePage(-1)"
          >
            <ChevronLeft :size="17" /></button
          ><span>{{ page }} / {{ data.pages }}</span
          ><button
            class="icon-button"
            aria-label="下一页"
            :disabled="page >= data.pages || loading"
            @click="changePage(1)"
          >
            <ChevronRight :size="17" />
          </button>
        </div>
      </footer>
    </section>
    <RequestDetails
      v-if="selected !== null"
      :id="selected"
      :key="selected"
      @close="selected = null"
    />
  </section>
</template>

<script setup>
import { ref } from "vue";
import { ChevronLeft, ChevronRight, Download, RefreshCw } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import ResourceNotice from "../components/ResourceNotice.vue";
import StatusBadge from "../components/StatusBadge.vue";
import RequestDetails from "../components/requests/RequestDetails.vue";
import RequestFilters from "../components/requests/RequestFilters.vue";
import { useRequestLogs } from "../composables/useRequestLogs";
import { dateTime, latency } from "../lib/format";
const {
  data,
  loading,
  error,
  refresh,
  filters,
  page,
  pageSize,
  updateFilters,
  activeCount,
  pendingSearch,
  pendingRange,
  rangeError,
  applySearch,
  applyRange,
  resetFilters,
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
        :disabled="exporting || loading || !!error || !data.total"
        @click="exportCsv"
      >
        <Download :size="16" />{{ exporting ? "导出中…" : "导出 CSV" }}
      </button></PageHeader
    >
    <ResourceNotice :error="error" @retry="refresh" />
    <section class="data-section">
      <RequestFilters
        :filters="filters"
        :options="data.filters"
        :active-count="activeCount"
        :pending-search="pendingSearch"
        :pending-range="pendingRange"
        :range-error="rangeError"
        @change="updateFilters"
        @search="applySearch"
        @range="applyRange"
        @reset="resetFilters"
      />
      <div class="table-shell" :aria-busy="loading">
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
                  <strong>{{
                    loading
                      ? "正在加载…"
                      : error
                        ? "请求记录加载失败"
                        : activeCount
                          ? "没有符合筛选条件的请求"
                          : "今天还没有请求"
                  }}</strong>
                  <small v-if="!loading && !error">{{
                    activeCount
                      ? "可以调整条件或重置筛选后再查看。"
                      : "可切换到近 24 小时或近 7 天查看历史记录。"
                  }}</small>
                  <button
                    v-if="!loading && error"
                    class="button secondary"
                    @click="refresh"
                  >
                    重新加载
                  </button>
                  <button
                    v-else-if="!loading && activeCount"
                    class="button secondary"
                    @click="resetFilters"
                  >
                    重置筛选
                  </button>
                </div>
              </td>
            </tr>
            <tr v-for="item in data.items" :key="item.id">
              <td data-label="时间 / 来源">
                <strong>{{ dateTime(item.created_at) }}</strong
                ><small :title="item.client">{{ item.client }}</small>
              </td>
              <td data-label="状态"><StatusBadge :state="item.status" /></td>
              <td data-label="模型 / 凭证">
                <strong :title="item.model">{{ item.model }}</strong
                ><small :title="item.credential">{{
                  item.credential || "—"
                }}</small>
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
                <p class="truncate" :title="item.request_preview">
                  {{ item.request_preview }}
                </p>
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
        <span
          >{{
            data.total
              ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, data.total)} / 共 ${data.total} 条`
              : "共 0 条"
          }}
          · 每 5 秒更新</span
        >
        <label class="page-size"
          >每页<select v-model.number="pageSize" aria-label="每页条数">
            <option :value="20">20 条</option>
            <option :value="50">50 条</option>
            <option :value="100">100 条</option>
          </select></label
        >
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

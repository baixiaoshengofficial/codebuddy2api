<script setup>
import { computed, ref, watch } from "vue";
import { ArrowUpRight, Copy, RefreshCw } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import ResourceNotice from "../components/ResourceNotice.vue";
import { useResource } from "../composables/useResource";
import { consoleApi } from "../services/console";
import { copyText } from "../lib/api";
import { notify } from "../lib/notify";
import { latency } from "../lib/format";
const period = ref("today");
const { data, loading, error, refresh } = useResource(
  async () => {
    const [summary, routing] = await Promise.all([
      consoleApi.overview(
        new URLSearchParams({ period: period.value, offset: 480 }),
      ),
      consoleApi.credentials(),
    ]);
    return { summary, routing };
  },
  { summary: {}, routing: { credentials: [], available: 0 } },
  15000,
);
watch(period, refresh);
const metrics = computed(() => [
  [
    "请求总数",
    data.value.summary.requests ?? "—",
    `上游尝试 ${data.value.summary.upstream_attempts ?? "—"} 次`,
  ],
  [
    "成功率",
    data.value.summary.success_rate == null
      ? "—"
      : `${data.value.summary.success_rate}%`,
    `处理中 ${data.value.summary.pending ?? "—"} 条`,
  ],
  [
    "平均耗时",
    latency(data.value.summary.average_latency_ms),
    "按已完成请求计算",
  ],
  [
    "可用凭证",
    `${data.value.routing.available} / ${data.value.routing.credentials.length}`,
    "包含可自动续期的凭证",
  ],
]);
const entries = (object) =>
  Object.entries(object || {}).sort((a, b) => b[1] - a[1]);
const endpoint = `${window.location.origin}/codebuddy/v1`;
async function copy() {
  try {
    await copyText(endpoint);
    notify("API 地址已复制", "success");
  } catch {
    notify("复制失败", "error");
  }
}
</script>
<template>
  <section class="view-stack">
    <PageHeader
      title="运行概览"
      eyebrow="OVERVIEW"
      description="了解服务的请求表现与凭证可用情况。"
      ><select v-model="period" aria-label="概览时间范围">
        <option value="today">今天（北京时间）</option>
        <option value="24h">近 24 小时</option>
        <option value="7d">近 7 天</option></select
      ><button class="button secondary" :disabled="loading" @click="refresh">
        <RefreshCw :size="16" :class="{ spin: loading }" />刷新
      </button></PageHeader
    ><ResourceNotice :error="error" @retry="refresh" />
    <div class="metric-grid">
      <article
        v-for="[title, value, subtitle] in metrics"
        :key="title"
        class="metric-card"
      >
        <span>{{ title }}<ArrowUpRight :size="17" /></span
        ><strong>{{ value }}</strong
        ><small>{{ subtitle }}</small>
      </article>
    </div>
    <section class="endpoint-banner">
      <div>
        <p class="eyebrow">OPENAI COMPATIBLE</p>
        <h3>一个端点，连接你的工作流。</h3>
        <code>{{ endpoint }}</code>
      </div>
      <button class="button secondary" @click="copy">
        <Copy :size="16" />复制 API 地址
      </button>
    </section>
    <div class="data-grid two-columns">
      <section
        v-for="[title, source] in [
          ['模型使用', data.summary.models],
          ['调用来源', data.summary.clients],
        ]"
        :key="title"
        class="data-section"
      >
        <header>
          <h3>{{ title }}</h3>
          <small>{{
            period === "today"
              ? "今天 · UTC+8"
              : period === "24h"
                ? "近 24 小时"
                : "近 7 天"
          }}</small>
        </header>
        <div v-if="!entries(source).length" class="empty-state">
          当前范围暂无请求
        </div>
        <div v-else class="usage-list">
          <div v-for="[name, count] in entries(source)" :key="name">
            <div>
              <strong>{{ name }}</strong
              ><span>{{ count }} 次</span>
            </div>
            <meter
              :value="count"
              :max="data.summary.requests || 1"
              :aria-label="`${name} 请求占比`"
            />
          </div>
        </div>
      </section>
    </div>
    <p class="muted">
      统计基于保留的请求日志；Tokens
      中的估算值和上游尝试次数均不能用于确认实际扣费。
    </p>
  </section>
</template>

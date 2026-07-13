<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Activity, Copy, KeyRound, Link2, RefreshCw, Route, Server } from '@lucide/vue'
import { apiFetch, copyText } from '../lib/api'
import { notify } from '../lib/notify'

const loading = ref(true)
const refreshing = ref(false)
const healthy = ref(false)
const credentials = ref([])
const stats = ref({ model_usage: {}, credential_usage: {}, client_usage: {} })
let timer

const endpoint = `${window.location.origin}/codebuddy/v1`
const validCredentials = computed(() => credentials.value.filter((item) => !item.is_expired).length)
const totalCalls = computed(() => Object.values(stats.value.model_usage || {}).reduce((sum, count) => sum + Number(count), 0))

function sortedEntries(value) {
  return Object.entries(value || {}).sort((a, b) => Number(b[1]) - Number(a[1]))
}

async function load(silent = false) {
  if (!silent) refreshing.value = true
  try {
    const [healthResult, credentialResult, statsResult] = await Promise.allSettled([
      apiFetch('/health', {}, false),
      apiFetch('/codebuddy/v1/credentials'),
      apiFetch('/api/stats'),
    ])
    healthy.value = healthResult.status === 'fulfilled'
    if (credentialResult.status === 'fulfilled') credentials.value = credentialResult.value.credentials || []
    if (statsResult.status === 'fulfilled') stats.value = statsResult.value
    if (!silent && [healthResult, credentialResult, statsResult].some((item) => item.status === 'rejected')) {
      notify('部分概览数据加载失败', 'warning')
    }
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function copyEndpoint() {
  await copyText(endpoint)
  notify('API 端点已复制', 'success')
}

onMounted(() => {
  load()
  timer = window.setInterval(() => load(true), 30000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="view-stack">
    <div class="view-toolbar">
      <div>
        <p class="eyebrow">Service overview</p>
        <h2>运行概览</h2>
      </div>
      <button class="button secondary" :disabled="refreshing" @click="load()">
        <RefreshCw :size="17" :class="{ spin: refreshing }" />刷新
      </button>
    </div>

    <div class="metric-grid" :class="{ loading }">
      <article class="metric-card">
        <div class="metric-icon blue"><KeyRound :size="20" /></div>
        <span>有效凭证</span>
        <strong>{{ validCredentials }} / {{ credentials.length }}</strong>
        <small>{{ credentials.length ? '凭证池可用' : '等待添加凭证' }}</small>
      </article>
      <article class="metric-card">
        <div class="metric-icon green"><Server :size="20" /></div>
        <span>服务状态</span>
        <strong>{{ healthy ? '运行正常' : '连接异常' }}</strong>
        <small>{{ healthy ? '健康检查通过' : '请检查服务日志' }}</small>
      </article>
      <article class="metric-card endpoint-card" @click="copyEndpoint">
        <div class="metric-icon amber"><Link2 :size="20" /></div>
        <span>API 端点</span>
        <strong class="endpoint-value">{{ endpoint }}</strong>
        <small><Copy :size="13" /> 点击复制</small>
      </article>
      <article class="metric-card">
        <div class="metric-icon violet"><Activity :size="20" /></div>
        <span>累计请求</span>
        <strong>{{ totalCalls.toLocaleString() }}</strong>
        <small>持久化统计记录</small>
      </article>
    </div>

    <div class="data-grid three-columns">
      <section class="data-section">
        <header><h3>模型使用</h3><Route :size="18" /></header>
        <div v-if="sortedEntries(stats.model_usage).length" class="compact-list">
          <div v-for="([name, count]) in sortedEntries(stats.model_usage)" :key="name">
            <span>{{ name }}</span><strong>{{ count }}</strong>
          </div>
        </div>
        <p v-else class="empty-state">暂无数据</p>
      </section>
      <section class="data-section">
        <header><h3>调用来源</h3><Activity :size="18" /></header>
        <div v-if="sortedEntries(stats.client_usage).length" class="compact-list">
          <div v-for="([name, count]) in sortedEntries(stats.client_usage)" :key="name">
            <span>{{ name }}</span><strong>{{ count }}</strong>
          </div>
        </div>
        <p v-else class="empty-state">暂无数据</p>
      </section>
      <section class="data-section">
        <header><h3>凭证使用</h3><KeyRound :size="18" /></header>
        <div v-if="sortedEntries(stats.credential_usage).length" class="compact-list">
          <div v-for="([name, count]) in sortedEntries(stats.credential_usage)" :key="name">
            <span :title="name">{{ name.split('/').pop() }}</span><strong>{{ count }}</strong>
          </div>
        </div>
        <p v-else class="empty-state">暂无数据</p>
      </section>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ChevronLeft, ChevronRight, Download, RefreshCw, Search } from '@lucide/vue'
import { apiFetch, authHeaders } from '../lib/api'
import { notify } from '../lib/notify'

const loading = ref(true)
const data = ref({ items: [], page: 1, pages: 1, total: 0, filters: {} })
const filters = reactive({ days: 1, model: '', client: '', credential: '', status: '' })
let timer
let sequence = 0

const params = computed(() => {
  const value = new URLSearchParams({ page: data.value.page || 1, page_size: 20, days: filters.days })
  for (const key of ['model', 'client', 'credential', 'status']) {
    if (filters[key]) value.set(key, filters[key])
  }
  return value
})

function formatDate(value) {
  return new Date(Number(value)).toLocaleString('zh-CN', { hour12: false })
}

function formatLatency(value) {
  if (value == null) return '-'
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value}ms`
}

function currentRequestPreview(value) {
  const text = String(value || '')
  const markers = [...text.matchAll(/(?:^|\s)(User|Tool|Input):\s/g)]
  if (!markers.length) return text
  const marker = markers[markers.length - 1]
  const start = marker.index + marker[0].length
  const tail = text.slice(start)
  const nextRole = tail.search(/\s(?:User|Assistant|Tool|Input):\s/)
  return (nextRole >= 0 ? tail.slice(0, nextRole) : tail).trim()
}

async function load(silent = false) {
  const current = ++sequence
  if (!silent) loading.value = true
  try {
    const result = await apiFetch(`/api/request-logs?${params.value}`)
    if (current === sequence) data.value = result
  } catch (error) {
    if (!silent) notify(`请求明细加载失败：${error.message}`, 'error')
  } finally {
    if (current === sequence) loading.value = false
  }
}

function applyFilters() {
  data.value.page = 1
  load()
}

function setDays(days) {
  filters.days = days
  applyFilters()
}

function changePage(delta) {
  const next = data.value.page + delta
  if (next < 1 || next > data.value.pages) return
  data.value.page = next
  load()
}

async function exportCsv() {
  const exportParams = new URLSearchParams({ days: filters.days })
  for (const key of ['model', 'client', 'credential', 'status']) {
    if (filters[key]) exportParams.set(key, filters[key])
  }
  try {
    const response = await fetch(`/api/request-logs/export?${exportParams}`, { headers: authHeaders() })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const blob = await response.blob()
    const disposition = response.headers.get('content-disposition') || ''
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] || 'codebuddy-request-logs.csv'
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = filename
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (error) {
    notify(`导出失败：${error.message}`, 'error')
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(() => load(true), 5000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="view-stack request-view">
    <div class="view-toolbar">
      <div>
        <p class="eyebrow">Request audit</p>
        <h2>请求明细</h2>
      </div>
      <div class="toolbar-actions">
        <button class="icon-button" title="刷新" @click="load()"><RefreshCw :size="18" :class="{ spin: loading }" /></button>
        <button class="button secondary" @click="exportCsv"><Download :size="17" />导出</button>
      </div>
    </div>

    <div class="filter-bar">
      <div class="segmented-control" aria-label="时间范围">
        <button v-for="day in [1, 7, 30]" :key="day" :class="{ active: filters.days === day }" @click="setDays(day)">
          {{ day === 1 ? '今天' : `${day} 天` }}
        </button>
      </div>
      <select v-model="filters.model" aria-label="模型" @change="applyFilters">
        <option value="">全部模型</option>
        <option v-for="item in data.filters.models || []" :key="item">{{ item }}</option>
      </select>
      <select v-model="filters.client" aria-label="来源" @change="applyFilters">
        <option value="">全部来源</option>
        <option v-for="item in data.filters.clients || []" :key="item">{{ item }}</option>
      </select>
      <select v-model="filters.credential" aria-label="凭证" @change="applyFilters">
        <option value="">全部凭证</option>
        <option v-for="item in data.filters.credentials || []" :key="item">{{ item }}</option>
      </select>
      <select v-model="filters.status" aria-label="状态" @change="applyFilters">
        <option value="">全部状态</option>
        <option value="success">成功</option>
        <option value="error">失败</option>
        <option value="pending">处理中</option>
      </select>
    </div>

    <div class="table-shell">
      <table class="data-table request-table">
        <thead>
          <tr><th>时间</th><th>状态</th><th>模型</th><th>调用来源</th><th>凭证</th><th>Tokens</th><th>耗时</th><th>本轮输入 / 输出</th></tr>
        </thead>
        <tbody>
          <tr v-if="loading && !data.items.length"><td colspan="8"><div class="empty-state"><RefreshCw class="spin" :size="20" />加载中</div></td></tr>
          <tr v-else-if="!data.items.length"><td colspan="8"><div class="empty-state"><Search :size="20" />当前范围没有请求</div></td></tr>
          <tr v-for="item in data.items" :key="item.id">
            <td class="nowrap">{{ formatDate(item.created_at) }}</td>
            <td><span class="status-badge" :class="item.status">{{ item.status === 'success' ? '成功' : item.status === 'error' ? '失败' : '处理中' }}</span></td>
            <td><strong>{{ item.model }}</strong><small>{{ item.endpoint }}</small></td>
            <td><strong>{{ item.client }}</strong><small>{{ [item.client_detail, item.client_host].filter(Boolean).join(' · ') }}</small></td>
            <td><span class="truncate" :title="item.credential">{{ item.credential || '本地' }}</span><small>{{ item.credential_user_id }}</small></td>
            <td class="nowrap"><strong>{{ item.token_estimated ? '~' : '' }}{{ item.total_tokens ?? '-' }}</strong><small>in {{ item.input_tokens ?? '-' }} / out {{ item.output_tokens ?? '-' }}</small></td>
            <td class="nowrap">{{ formatLatency(item.latency_ms) }}</td>
            <td class="request-cell">
              <div class="request-pair">
                <div class="request-message">
                  <b>User</b>
                  <span :title="currentRequestPreview(item.request_preview)">{{ currentRequestPreview(item.request_preview) }}</span>
                </div>
                <div v-if="item.response_preview" class="request-message response">
                  <b>Assistant</b>
                  <span :title="item.response_preview">{{ item.response_preview }}</span>
                </div>
              </div>
              <small>ID {{ String(item.request_id || '').slice(0, 12) }} · Attempts {{ item.upstream_attempts || 1 }}</small>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="pagination">
      <span>共 {{ data.total }} 条</span>
      <div>
        <button class="icon-button compact" title="上一页" :disabled="data.page <= 1" @click="changePage(-1)"><ChevronLeft :size="17" /></button>
        <span>第 {{ data.page }} / {{ data.pages }} 页</span>
        <button class="icon-button compact" title="下一页" :disabled="data.page >= data.pages" @click="changePage(1)"><ChevronRight :size="17" /></button>
      </div>
    </footer>
  </section>
</template>

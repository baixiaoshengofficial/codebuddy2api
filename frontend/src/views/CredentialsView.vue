<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  AlertCircle,
  CalendarClock,
  Check,
  CircleCheck,
  Clipboard,
  ExternalLink,
  Gift,
  KeyRound,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Trash2,
} from '@lucide/vue'
import { apiFetch, copyText } from '../lib/api'
import { notify } from '../lib/notify'

const mode = ref('list')
const loading = ref(true)
const busy = ref(false)
const credentials = ref([])
const current = ref({ status: 'unknown', index: -1 })
const models = ref([])
const token = ref('')
const userId = ref('')
const authUrl = ref('')
const authState = ref('')
const authPending = ref(false)
const checkin = ref({ enabled: false, running: false, accounts: [] })
const checkinBusy = ref(false)
let pollTimer
let checkinPollTimer

const currentLabel = computed(() => {
  const labels = {
    manual_selected: '手动选择模式',
    auto_rotation_disabled: '自动轮换已关闭',
    rotation_count_zero: '轮换频率为 0',
    auto_rotation: '自动轮换中',
    no_credentials: '暂无可用凭证',
  }
  return labels[current.value.status] || '状态未知'
})

const rotationAction = computed(() => {
  if (current.value.status === 'manual_selected') return { label: '恢复自动轮换', icon: RotateCcw }
  if (current.value.auto_rotation_enabled === false) return { label: '开启自动轮换', icon: Play }
  return { label: '关闭自动轮换', icon: Pause }
})

const credentialNames = computed(() => Object.fromEntries(
  credentials.value.map((item) => [item.filename, item.email || item.user_id || item.filename]),
))

const checkinCompleteCount = computed(() => checkin.value.accounts.filter(
  (item) => ['claimed', 'already_checked_in'].includes(item.status),
).length)

function formatExpiry(credential) {
  if (!credential.expires_at) return '长期有效'
  return new Date(Number(credential.expires_at) * 1000).toLocaleDateString('zh-CN')
}

function formatTime(timestamp) {
  if (!timestamp) return '尚未执行'
  return new Date(Number(timestamp) * 1000).toLocaleString('zh-CN', { hour12: false })
}

function checkinLabel(status) {
  return {
    claimed: '领取成功',
    already_checked_in: '今日已签',
    inactive: '活动未开放',
    expired: '凭证已过期',
    error: '检查失败',
    pending: '等待检查',
  }[status] || '状态未知'
}

function checkinClass(status) {
  if (['claimed', 'already_checked_in'].includes(status)) return 'success'
  if (['error', 'expired'].includes(status)) return 'error'
  return 'pending'
}

async function load() {
  loading.value = true
  try {
    const [credentialData, currentData] = await Promise.all([
      apiFetch('/codebuddy/v1/credentials'),
      apiFetch('/codebuddy/v1/credentials/current'),
    ])
    credentials.value = credentialData.credentials || []
    current.value = currentData
  } catch (error) {
    notify(`凭证加载失败：${error.message}`, 'error')
  } finally {
    loading.value = false
  }
}

async function loadModels() {
  try {
    const result = await apiFetch('/codebuddy/v1/models')
    models.value = (result.data || []).map((item) => item.id)
  } catch {
    models.value = []
  }
}

async function loadCheckin(silent = false) {
  try {
    checkin.value = await apiFetch('/api/checkin')
  } catch (error) {
    if (!silent) notify(`签到状态加载失败：${error.message}`, 'error')
  }
}

async function runCheckin() {
  checkinBusy.value = true
  try {
    checkin.value = await apiFetch('/api/checkin/run', { method: 'POST' })
    const claimed = checkin.value.accounts.filter((item) => item.status === 'claimed').length
    notify(claimed ? `签到完成，本次领取 ${claimed} 个账号` : '签到检查完成，没有待领取账号', claimed ? 'success' : 'info')
  } catch (error) {
    notify(`签到执行失败：${error.message}`, 'error')
  } finally {
    checkinBusy.value = false
  }
}

async function selectCredential(index) {
  busy.value = true
  try {
    await apiFetch('/codebuddy/v1/credentials/select', { method: 'POST', body: JSON.stringify({ index }) })
    notify(`已切换到凭证 #${index + 1}`, 'success')
    await load()
  } catch (error) {
    notify(`切换失败：${error.message}`, 'error')
  } finally {
    busy.value = false
  }
}

async function deleteCredential(index) {
  if (!window.confirm(`确定删除凭证 #${index + 1}？此操作不可恢复。`)) return
  busy.value = true
  try {
    await apiFetch('/codebuddy/v1/credentials/delete', { method: 'POST', body: JSON.stringify({ index }) })
    notify('凭证已删除', 'success')
    await load()
  } catch (error) {
    notify(`删除失败：${error.message}`, 'error')
  } finally {
    busy.value = false
  }
}

async function toggleRotation() {
  busy.value = true
  try {
    const path = current.value.status === 'manual_selected'
      ? '/codebuddy/v1/credentials/auto'
      : '/codebuddy/v1/credentials/toggle-rotation'
    await apiFetch(path, { method: 'POST' })
    notify('轮换状态已更新', 'success')
    await load()
  } catch (error) {
    notify(`操作失败：${error.message}`, 'error')
  } finally {
    busy.value = false
  }
}

async function testCredential(index) {
  busy.value = true
  try {
    if (!models.value.length) await loadModels()
    if (!models.value.length) throw new Error('没有可用模型')
    await apiFetch('/codebuddy/v1/credentials/select', { method: 'POST', body: JSON.stringify({ index }) })
    await apiFetch('/codebuddy/v1/chat/completions', {
      method: 'POST',
      body: JSON.stringify({ model: models.value[0], messages: [{ role: 'user', content: 'test' }], max_tokens: 1 }),
    })
    notify(`凭证 #${index + 1} 测试成功，已设为当前凭证`, 'success')
    await load()
  } catch (error) {
    notify(`凭证测试失败：${error.message}`, 'error')
  } finally {
    busy.value = false
  }
}

async function addCredential() {
  if (!token.value.trim()) return
  busy.value = true
  try {
    await apiFetch('/codebuddy/v1/credentials', {
      method: 'POST',
      body: JSON.stringify({ bearer_token: token.value.trim(), user_id: userId.value.trim() || undefined }),
    })
    token.value = ''
    userId.value = ''
    mode.value = 'list'
    notify('凭证添加成功', 'success')
    await load()
  } catch (error) {
    notify(`添加失败：${error.message}`, 'error')
  } finally {
    busy.value = false
  }
}

async function startAuth() {
  authPending.value = true
  stopPolling()
  try {
    const result = await apiFetch('/codebuddy/auth/start', {}, false)
    authUrl.value = result.verification_uri_complete || ''
    authState.value = result.auth_state || ''
    if (!authUrl.value || !authState.value) throw new Error(result.message || '未返回认证链接')
    pollToken()
  } catch (error) {
    authPending.value = false
    notify(`认证启动失败：${error.message}`, 'error')
  }
}

function pollToken() {
  const poll = async () => {
    try {
      const result = await apiFetch('/codebuddy/auth/poll', {
        method: 'POST',
        body: JSON.stringify({ auth_state: authState.value }),
      }, false)
      if (result.access_token) {
        stopPolling()
        authPending.value = false
        authUrl.value = ''
        mode.value = 'list'
        notify('授权成功，凭证已保存', 'success')
        await load()
      }
    } catch (error) {
      if (['authorization_pending', 'slow_down'].includes(error.message)) return
      stopPolling()
      authPending.value = false
      notify(`授权失败：${error.message}`, 'error')
    }
  }
  poll()
  pollTimer = window.setInterval(poll, 5000)
}

function stopPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = null
}

async function copyAuthUrl() {
  await copyText(authUrl.value)
  notify('认证链接已复制', 'success')
}

onMounted(() => {
  load()
  loadModels()
  loadCheckin()
  checkinPollTimer = window.setInterval(() => loadCheckin(true), 30000)
})
onBeforeUnmount(() => {
  stopPolling()
  if (checkinPollTimer) window.clearInterval(checkinPollTimer)
})
</script>

<template>
  <section class="view-stack">
    <div class="view-toolbar">
      <div>
        <p class="eyebrow">Credential pool</p>
        <h2>凭证管理</h2>
      </div>
      <div class="segmented-control">
        <button :class="{ active: mode === 'list' }" @click="mode = 'list'">账号列表</button>
        <button :class="{ active: mode === 'add' }" @click="mode = 'add'">添加账号</button>
      </div>
    </div>

    <template v-if="mode === 'list'">
      <section class="status-strip" :class="current.status === 'auto_rotation' ? 'success' : 'warning'">
        <div>
          <ShieldCheck :size="20" />
          <span><strong>{{ currentLabel }}</strong><small v-if="current.filename">{{ current.filename }} · {{ current.user_id || '未知用户' }}</small></span>
        </div>
        <button class="button secondary" :disabled="busy || current.status === 'no_credentials'" @click="toggleRotation">
          <component :is="rotationAction.icon" :size="16" />{{ rotationAction.label }}
        </button>
      </section>

      <section class="checkin-section">
        <header>
          <div class="checkin-title">
            <span class="metric-icon amber"><Gift :size="18" /></span>
            <span>
              <strong>每日签到</strong>
              <small>{{ checkin.enabled ? `自动签到已开启 · 每天 ${checkin.schedule_time || '11:00'}（北京时间）` : '自动签到已关闭' }}</small>
            </span>
          </div>
          <div class="checkin-actions">
            <span>{{ checkinCompleteCount }} / {{ checkin.accounts.length }} 今日完成</span>
            <button class="button secondary" :disabled="checkinBusy || checkin.running || !credentials.length" @click="runCheckin">
              <RefreshCw :size="16" :class="{ spin: checkinBusy || checkin.running }" />
              {{ checkinBusy || checkin.running ? '检查中' : '立即检查' }}
            </button>
          </div>
        </header>
        <div v-if="!checkin.accounts.length" class="empty-state"><CalendarClock :size="21" />添加凭证后自动检查签到</div>
        <div v-else class="checkin-list">
          <article v-for="account in checkin.accounts" :key="account.filename" class="checkin-row">
            <CircleCheck v-if="['claimed', 'already_checked_in'].includes(account.status)" :size="20" class="checkin-success" />
            <AlertCircle v-else-if="['error', 'expired'].includes(account.status)" :size="20" class="checkin-error" />
            <CalendarClock v-else :size="20" class="checkin-pending" />
            <div class="checkin-account">
              <strong>{{ credentialNames[account.filename] || account.user_id }}</strong>
              <small>{{ account.message }}</small>
            </div>
            <div class="checkin-meta"><span>连续签到</span><strong>{{ account.streak_days || 0 }} 天</strong></div>
            <div class="checkin-meta"><span>今日奖励</span><strong>{{ account.today_credit || account.daily_credit || 0 }} Credits</strong></div>
            <div class="checkin-meta"><span>检查时间</span><strong>{{ formatTime(account.checked_at) }}</strong></div>
            <span class="status-badge" :class="checkinClass(account.status)">{{ checkinLabel(account.status) }}</span>
          </article>
        </div>
      </section>

      <div class="section-heading">
        <div><h3>账号列表</h3><span>{{ credentials.length }} 个凭证</span></div>
        <button class="icon-button" title="刷新凭证" @click="load"><RefreshCw :size="18" :class="{ spin: loading }" /></button>
      </div>

      <div class="credential-list">
        <div v-if="loading && !credentials.length" class="empty-state"><LoaderCircle class="spin" :size="22" />加载中</div>
        <div v-else-if="!credentials.length" class="empty-state"><KeyRound :size="24" />暂无凭证</div>
        <article v-for="(credential, index) in credentials" :key="credential.filename || index" class="credential-row" :class="{ selected: current.index === index }">
          <div class="credential-state" :class="credential.is_expired ? 'expired' : 'valid'">
            <Check v-if="!credential.is_expired" :size="18" />
            <KeyRound v-else :size="18" />
          </div>
          <div class="credential-primary">
            <div><strong>{{ credential.email || credential.user_id || `凭证 #${index + 1}` }}</strong><span v-if="current.index === index" class="status-badge success">当前使用</span></div>
            <small>{{ credential.filename }}</small>
          </div>
          <div class="credential-meta"><span>到期时间</span><strong>{{ formatExpiry(credential) }}</strong></div>
          <div class="credential-meta"><span>剩余时间</span><strong>{{ credential.time_remaining_str || '未知' }}</strong></div>
          <div class="row-actions">
            <button v-if="current.index !== index" class="icon-button compact" title="选择凭证" :disabled="busy" @click="selectCredential(index)"><Play :size="16" /></button>
            <button class="icon-button compact" title="测试凭证" :disabled="busy" @click="testCredential(index)"><ShieldCheck :size="16" /></button>
            <button class="icon-button compact danger" title="删除凭证" :disabled="busy" @click="deleteCredential(index)"><Trash2 :size="16" /></button>
          </div>
        </article>
      </div>
    </template>

    <div v-else class="add-credential-grid">
      <section class="data-section form-section">
        <header><h3>CodeBuddy 授权</h3><ExternalLink :size="18" /></header>
        <p class="muted">通过 CodeBuddy 登录流程自动保存凭证。</p>
        <button class="button primary" :disabled="authPending" @click="startAuth">
          <LoaderCircle v-if="authPending" class="spin" :size="17" /><Play v-else :size="17" />
          {{ authPending ? '等待授权' : '开始授权' }}
        </button>
        <div v-if="authUrl" class="auth-link-box">
          <input :value="authUrl" readonly />
          <button class="icon-button compact" title="复制链接" @click="copyAuthUrl"><Clipboard :size="16" /></button>
          <a class="icon-button compact" title="打开授权页面" :href="authUrl" target="_blank" rel="noopener"><ExternalLink :size="16" /></a>
        </div>
      </section>

      <section class="data-section form-section">
        <header><h3>手动添加</h3><Plus :size="18" /></header>
        <label>Bearer Token<textarea v-model="token" rows="5" placeholder="粘贴 Bearer Token"></textarea></label>
        <label>用户 ID（可选）<input v-model="userId" placeholder="用于区分账号" /></label>
        <button class="button primary" :disabled="busy || !token.trim()" @click="addCredential"><Plus :size="17" />添加凭证</button>
      </section>
    </div>
  </section>
</template>

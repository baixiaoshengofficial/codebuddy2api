<script setup>
import { onMounted, ref } from 'vue'
import { LockKeyhole, RefreshCw, Save, Settings2 } from '@lucide/vue'
import { apiFetch } from '../lib/api'
import { notify } from '../lib/notify'

const readonlyKeys = new Set(['CODEBUDDY_CREDS_DIR', 'CODEBUDDY_LOG_LEVEL'])
const loading = ref(true)
const saving = ref(false)
const settings = ref({})
const labels = ref({})

async function load() {
  loading.value = true
  try {
    const data = await apiFetch('/api/settings')
    settings.value = { ...(data.settings || {}) }
    labels.value = data.labels || {}
  } catch (error) {
    notify(`设置加载失败：${error.message}`, 'error')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const editable = Object.fromEntries(Object.entries(settings.value).filter(([key]) => !readonlyKeys.has(key)))
    const result = await apiFetch('/api/settings', { method: 'POST', body: JSON.stringify({ settings: editable }) })
    notify(result.message || '设置已保存', 'success')
    await load()
  } catch (error) {
    notify(`保存失败：${error.message}`, 'error')
  } finally {
    saving.value = false
  }
}

function inputType(key) {
  return key.toLowerCase().includes('password') ? 'password' : 'text'
}

onMounted(load)
</script>

<template>
  <section class="view-stack settings-view">
    <div class="view-toolbar">
      <div><p class="eyebrow">Runtime configuration</p><h2>服务设置</h2></div>
      <button class="icon-button" title="重新加载" @click="load"><RefreshCw :size="18" :class="{ spin: loading }" /></button>
    </div>

    <section class="settings-section">
      <header><div><Settings2 :size="19" /><h3>运行参数</h3></div></header>
      <div v-if="loading" class="empty-state"><RefreshCw class="spin" :size="20" />加载中</div>
      <form v-else class="settings-form" @submit.prevent="save">
        <label v-for="(value, key) in settings" :key="key" class="setting-row">
          <span><strong>{{ labels[key] || key }}</strong><small>{{ key }}</small></span>
          <div class="setting-control">
            <select v-if="key === 'CODEBUDDY_SITE'" v-model="settings[key]" :disabled="readonlyKeys.has(key)">
              <option value="china">china - 国内站</option>
              <option value="international">international - 国际站</option>
            </select>
            <select v-else-if="key === 'CODEBUDDY_AUTO_CHECKIN'" v-model="settings[key]" :disabled="readonlyKeys.has(key)">
              <option value="true">开启</option>
              <option value="false">关闭</option>
            </select>
            <input
              v-else-if="key === 'CODEBUDDY_CHECKIN_TIME'"
              v-model="settings[key]"
              type="time"
              :disabled="readonlyKeys.has(key)"
            />
            <input
              v-else
              v-model="settings[key]"
              :type="inputType(key)"
              :disabled="readonlyKeys.has(key)"
            />
            <LockKeyhole v-if="readonlyKeys.has(key)" :size="16" title="由 .env 管理" />
          </div>
        </label>
        <div class="form-actions">
          <button class="button primary" type="submit" :disabled="saving"><Save :size="17" />{{ saving ? '保存中' : '保存设置' }}</button>
        </div>
      </form>
    </section>
  </section>
</template>

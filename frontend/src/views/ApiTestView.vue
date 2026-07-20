<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { Braces, Check, Clipboard, LoaderCircle, Play, RefreshCw, Terminal } from '@lucide/vue'
import { apiFetch, authHeaders, copyText } from '../lib/api'
import { notify } from '../lib/notify'

const models = ref([])
const model = ref('')
const message = ref('你好，请介绍一下自己。')
const stream = ref(true)
const running = ref(false)
const result = ref('')
const codeMode = ref('curl')

const availableModels = computed(() => models.value.map((item) => item.id))
const endpointPath = '/codebuddy/v1/chat/completions'
const endpoint = computed(() => `${window.location.origin}${endpointPath}`)
const requestBody = computed(() => ({
  model: model.value || 'glm-5.2',
  messages: [{ role: 'user', content: message.value }],
  stream: stream.value,
}))

const curlExample = computed(() => `curl ${endpoint.value} \\
  -H "Authorization: Bearer YOUR_PASSWORD" \\
  -H "Content-Type: application/json" \\
  -H "X-Client-Name: My Client" \\
  -d '${JSON.stringify(requestBody.value).replace(/'/g, `'"'"'`)}'`)

const pythonExample = computed(() => `from openai import OpenAI

client = OpenAI(
    api_key="YOUR_PASSWORD",
    base_url="${window.location.origin}/codebuddy/v1",
    default_headers={"X-Client-Name": "My Client"},
)

response = client.chat.completions.create(
    model="${requestBody.value.model}",
    messages=[{"role": "user", "content": ${JSON.stringify(message.value)}}],
    stream=${stream.value ? 'True' : 'False'},
)
print(response)`)

const currentExample = computed(() => codeMode.value === 'curl' ? curlExample.value : pythonExample.value)

async function loadModels() {
  try {
    const data = await apiFetch('/codebuddy/v1/models')
    models.value = data.data || []
  } catch (error) {
    notify(`模型加载失败：${error.message}`, 'error')
  }
}

watch(availableModels, (items) => {
  if (!items.includes(model.value)) model.value = items[0] || ''
}, { immediate: true })

async function runTest() {
  running.value = true
  result.value = ''
  try {
    const response = await fetch(endpointPath, {
      method: 'POST',
      headers: authHeaders({ 'X-Client-Name': 'Admin Console' }),
      body: JSON.stringify(requestBody.value),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body.detail || body.error?.message || `HTTP ${response.status}`)
    }
    if (!stream.value) {
      result.value = JSON.stringify(await response.json(), null, 2)
    } else {
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const value = line.slice(6).trim()
          if (!value || value === '[DONE]') continue
          try {
            result.value += `${JSON.stringify(JSON.parse(value), null, 2)}\n\n`
          } catch {
            result.value += `${value}\n`
          }
        }
      }
    }
    notify('API 调用成功', 'success')
  } catch (error) {
    result.value = `Error: ${error.message}`
    notify(`调用失败：${error.message}`, 'error')
  } finally {
    running.value = false
  }
}

async function copyExample() {
  await copyText(currentExample.value)
  notify('示例代码已复制', 'success')
}

onMounted(loadModels)
</script>

<template>
  <section class="view-stack">
    <div class="view-toolbar">
      <div><p class="eyebrow">OpenAI compatible</p><h2>API 测试</h2></div>
      <button class="icon-button" title="刷新模型" @click="loadModels"><RefreshCw :size="18" /></button>
    </div>

    <div class="api-test-grid">
      <section class="data-section form-section">
        <header><h3>请求</h3><Terminal :size="18" /></header>
        <label>模型<select v-model="model"><option v-for="item in availableModels" :key="item">{{ item }}</option></select></label>
        <label>消息<textarea v-model="message" rows="8" /></label>
        <label class="switch-row">
          <span><strong>流式响应</strong><small>使用 Server-Sent Events</small></span>
          <input v-model="stream" type="checkbox" role="switch" />
        </label>
        <button class="button primary" :disabled="running || !model || !message.trim()" @click="runTest">
          <LoaderCircle v-if="running" class="spin" :size="17" /><Play v-else :size="17" />{{ running ? '调用中' : '发送请求' }}
        </button>
      </section>

      <section class="data-section result-section">
        <header><h3>响应</h3><Check :size="18" /></header>
        <pre :class="{ placeholder: !result }">{{ result || '等待请求' }}</pre>
      </section>
    </div>

    <section class="code-section">
      <header>
        <div class="segmented-control">
          <button :class="{ active: codeMode === 'curl' }" @click="codeMode = 'curl'">cURL</button>
          <button :class="{ active: codeMode === 'python' }" @click="codeMode = 'python'">Python</button>
        </div>
        <button class="icon-button compact" title="复制代码" @click="copyExample"><Clipboard :size="16" /></button>
      </header>
      <pre><Braces :size="17" />{{ currentExample }}</pre>
    </section>
  </section>
</template>

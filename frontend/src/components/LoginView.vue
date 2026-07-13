<script setup>
import { ref } from 'vue'
import { ArrowRight, KeyRound, LoaderCircle } from '@lucide/vue'
import { apiFetch, setStoredPassword } from '../lib/api'

const emit = defineEmits(['authenticated'])
const password = ref('')
const error = ref('')
const loading = ref(false)

async function login() {
  if (!password.value) return
  loading.value = true
  error.value = ''
  setStoredPassword(password.value)
  try {
    await apiFetch('/codebuddy/v1/credentials')
    emit('authenticated')
  } catch {
    setStoredPassword('')
    error.value = '密码不正确，请重新输入。'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-layout">
    <section class="login-panel">
      <div class="brand-mark"><KeyRound :size="22" /></div>
      <div>
        <p class="eyebrow">CodeBuddy2API</p>
        <h1>管理控制台</h1>
        <p class="muted">使用服务访问密码登录。</p>
      </div>
      <form class="login-form" @submit.prevent="login">
        <label for="password">访问密码</label>
        <input id="password" v-model="password" type="password" autocomplete="current-password" autofocus />
        <p v-if="error" class="form-error">{{ error }}</p>
        <button class="button primary login-button" type="submit" :disabled="loading || !password">
          <LoaderCircle v-if="loading" class="spin" :size="18" />
          <ArrowRight v-else :size="18" />
          {{ loading ? '验证中' : '登录' }}
        </button>
      </form>
    </section>
  </main>
</template>

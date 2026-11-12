<script setup>
import { nextTick, ref } from "vue";
import {
  ArrowRight,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  ShieldCheck,
} from "@lucide/vue";
import { apiFetch, setStoredPassword } from "../lib/api";
const emit = defineEmits(["authenticated"]);
const password = ref(""),
  error = ref(""),
  loading = ref(false),
  visible = ref(false),
  passwordInput = ref(null);
async function login() {
  if (!password.value || loading.value) return;
  loading.value = true;
  error.value = "";
  setStoredPassword(password.value);
  try {
    await apiFetch("/codebuddy/v1/credentials");
    emit("authenticated");
  } catch (cause) {
    setStoredPassword("");
    error.value = [401, 403].includes(cause.status)
      ? "密码不正确，请重新输入。"
      : "暂时无法连接服务，请稍后重试。";
  } finally {
    loading.value = false;
    await nextTick();
    if (error.value) passwordInput.value?.focus();
  }
}
</script>
<template>
  <main class="login-layout">
    <section class="login-panel">
      <div class="login-brand">
        <span class="brand-mark"
          ><KeyRound :size="22" aria-hidden="true" /></span
        ><strong>CodeBuddy2API</strong>
      </div>
      <div>
        <p class="eyebrow">管理控制台</p>
        <h1>登录工作空间</h1>
        <p class="page-description">管理账号授权、剩余额度与请求记录。</p>
      </div>
      <form class="login-form" @submit.prevent="login">
        <label for="password">访问密码</label>
        <div class="password-field">
          <input
            id="password"
            ref="passwordInput"
            v-model="password"
            :type="visible ? 'text' : 'password'"
            autocomplete="current-password"
            :disabled="loading"
            :aria-invalid="Boolean(error)"
            :aria-describedby="error ? 'login-error' : 'login-hint'"
            required
            autofocus
          /><button
            type="button"
            class="icon-button"
            :aria-label="visible ? '隐藏密码' : '显示密码'"
            :aria-pressed="visible"
            @click="visible = !visible"
          >
            <EyeOff v-if="visible" :size="18" aria-hidden="true" /><Eye
              v-else
              :size="18"
              aria-hidden="true"
            />
          </button>
        </div>
        <small id="login-hint">使用服务器配置的服务访问密码。</small>
        <p v-if="error" id="login-error" class="form-error" role="alert">
          {{ error }}
        </p>
        <button
          class="button primary login-button"
          type="submit"
          :disabled="loading || !password"
        >
          <LoaderCircle
            v-if="loading"
            class="spin"
            :size="18"
            aria-hidden="true"
          /><ArrowRight v-else :size="18" aria-hidden="true" />{{
            loading ? "正在验证…" : "登录控制台"
          }}
        </button>
      </form>
      <p class="login-footer">
        <ShieldCheck
          :size="16"
          aria-hidden="true"
        />凭证保存在服务器，授权令牌不会显示在页面中。
      </p>
    </section>
  </main>
</template>

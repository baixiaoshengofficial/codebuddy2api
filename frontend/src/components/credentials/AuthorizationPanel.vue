<script setup>
import { ref } from "vue";
import { ExternalLink, Plus, RefreshCw } from "@lucide/vue";
import AppDrawer from "../AppDrawer.vue";
import { useDeviceAuthorization } from "../../composables/useDeviceAuthorization";
import { copyText } from "../../lib/api";
import { credentialLabel } from "../../lib/format";
import { notify } from "../../lib/notify";
const props = defineProps({
  product: { type: String, required: true },
  credential: { type: Object, default: null },
  disabled: Boolean,
});
const emit = defineEmits(["saved"]);
const visible = ref(false);
const { pending, url, error, start, cancel } = useDeviceAuthorization(
  props.product,
  () => {
    visible.value = false;
    emit("saved");
  },
);
function begin() {
  visible.value = true;
  start(props.credential?.filename);
}
function close() {
  cancel();
  visible.value = false;
}
async function copy() {
  try {
    await copyText(url.value);
    notify("授权链接已复制", "success");
  } catch {
    notify("复制失败，请手动复制链接", "error");
  }
}
</script>
<template>
  <div class="authorization">
    <button
      :class="['button', credential ? 'secondary' : 'primary']"
      :disabled="disabled || pending"
      :aria-label="
        credential ? `重新授权 ${credentialLabel(credential)}` : '添加凭证'
      "
      @click="begin"
    >
      <RefreshCw v-if="credential" :size="16" />
      <Plus v-else :size="16" />
      {{ pending ? "等待授权…" : credential ? "重新授权" : "添加凭证" }}
    </button>
    <AppDrawer
      v-if="visible"
      :title="`${credential ? '重新授权' : '授权'} ${product === 'codebuddy' ? 'CodeBuddy' : 'WorkBuddy'}`"
      :subtitle="credential ? credentialLabel(credential) : '添加账号凭证'"
      eyebrow="账号授权"
      @close="close"
    >
      <p v-if="credential" class="notice">
        请使用此条目的原账号登录。授权成功后会更新原凭证，不会新增条目。
      </p>
      <p v-else>打开链接完成登录，凭证保存后会自动刷新。</p>
      <small v-if="credential">{{ credential.filename }}</small>
      <p v-if="pending && !url" role="status">正在获取授权链接…</p>
      <p v-if="pending && url" role="status">
        等待登录授权，完成后会自动更新。
      </p>
      <div v-if="url" class="auth-link-box">
        <input :value="url" aria-label="授权链接" readonly />
        <button class="button secondary" @click="copy">复制</button>
      </div>
      <p v-if="error" role="alert" class="notice error">{{ error }}</p>
      <div class="toolbar-actions">
        <a
          v-if="url"
          class="button primary"
          :href="url"
          target="_blank"
          rel="noopener noreferrer"
          ><ExternalLink :size="15" />打开授权页</a
        >
        <button v-if="error" class="button primary" @click="begin">
          重新开始
        </button>
        <button class="button secondary" @click="close">
          {{ pending ? "取消等待" : "关闭" }}
        </button>
      </div>
    </AppDrawer>
  </div>
</template>

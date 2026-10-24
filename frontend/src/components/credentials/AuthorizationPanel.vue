<script setup>
import { ExternalLink, Plus } from "@lucide/vue";
import { useDeviceAuthorization } from "../../composables/useDeviceAuthorization";
import { copyText } from "../../lib/api";
import { notify } from "../../lib/notify";
const props = defineProps({ product: { type: String, required: true } });
const emit = defineEmits(["saved"]);
const { pending, url, error, start, cancel } = useDeviceAuthorization(
  props.product,
  () => emit("saved"),
);
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
    <button class="button primary" :disabled="pending" @click="start">
      <Plus :size="16" />{{ pending ? "等待授权…" : "添加凭证" }}
    </button>
    <div v-if="pending" class="authorization-popover">
      <strong
        >授权 {{ product === "codebuddy" ? "CodeBuddy" : "WorkBuddy" }}</strong
      >
      <p>打开链接完成登录，凭证保存后会自动刷新。</p>
      <div v-if="url" class="auth-link-box">
        <input :value="url" aria-label="授权链接" readonly /><button
          class="button secondary"
          @click="copy"
        >
          复制
        </button>
      </div>
      <div class="toolbar-actions">
        <a
          v-if="url"
          class="button primary"
          :href="url"
          target="_blank"
          rel="noopener noreferrer"
          ><ExternalLink :size="15" />打开授权页</a
        ><button class="button secondary" @click="cancel">取消等待</button>
      </div>
    </div>
    <p v-if="error" role="alert" class="form-error">{{ error }}</p>
  </div>
</template>

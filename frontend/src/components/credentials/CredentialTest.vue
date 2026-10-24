<script setup>
import { ref, watch } from "vue";
import { Play, Square } from "@lucide/vue";
import AppDrawer from "../AppDrawer.vue";
import ResourceNotice from "../ResourceNotice.vue";
import StatusBadge from "../StatusBadge.vue";
import { useChatTest } from "../../composables/useChatTest";
import { useResource } from "../../composables/useResource";
import { consoleApi } from "../../services/console";
import { credentialLabel } from "../../lib/format";
const props = defineProps({ credential: Object });
defineEmits(["close"]);
const {
  data: models,
  error: modelError,
  refresh,
} = useResource(consoleApi.models, []);
const model = ref("");
watch(models, (items) => {
  if (!model.value) model.value = items[0]?.id || "";
});
const { status, content, error, send, cancel } = useChatTest();
function test() {
  send({
    model: model.value,
    messages: [{ role: "user", content: "请回复 OK。" }],
    max_tokens: 16,
    stream: false,
    _credential_filename: props.credential.filename,
  });
}
</script>
<template>
  <AppDrawer
    title="测试凭证"
    :subtitle="credentialLabel(credential)"
    @close="$emit('close')"
  >
    <p class="notice">
      此测试会使用指定凭证调用真实模型，可能消耗积分。测试不会修改当前路由，也不会切换到其他凭证。
    </p>
    <ResourceNotice :error="modelError" @retry="refresh" />
    <label
      >模型<select v-model="model" :disabled="status === 'running'">
        <option v-for="item in models" :key="item.id">{{ item.id }}</option>
      </select></label
    >
    <button
      v-if="status !== 'running'"
      class="button primary"
      :disabled="!model"
      @click="test"
    >
      <Play :size="16" />开始模型测试
    </button>
    <button v-else class="button secondary" @click="cancel">
      <Square :size="16" />停止接收
    </button>
    <StatusBadge
      v-if="status !== 'idle'"
      :state="status"
      :label="
        {
          running: '测试中',
          success: '测试成功',
          error: '测试失败',
          cancelled: '已停止',
        }[status]
      "
    />
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <pre v-if="content" class="message-preview">{{ content }}</pre>
  </AppDrawer>
</template>

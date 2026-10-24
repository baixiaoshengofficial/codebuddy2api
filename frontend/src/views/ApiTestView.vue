<script setup>
import { computed, ref, watch } from "vue";
import { Copy, Play, Square } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import ResourceNotice from "../components/ResourceNotice.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { useResource } from "../composables/useResource";
import { useChatTest } from "../composables/useChatTest";
import { consoleApi } from "../services/console";
import { copyText } from "../lib/api";
import { notify } from "../lib/notify";
import { requestExample } from "../lib/examples";
import { latency } from "../lib/format";
const {
  data: models,
  error: modelError,
  refresh,
} = useResource(consoleApi.models, []);
const model = ref(""),
  message = ref("你好，请介绍一下自己。"),
  stream = ref(true),
  rawVisible = ref(false);
const { status, content, reasoning, raw, error, duration, send, cancel } =
  useChatTest();
watch(models, (items) => {
  if (!items.some((item) => item.id === model.value))
    model.value = items[0]?.id || "";
});
const body = computed(() => ({
  model: model.value,
  messages: [{ role: "user", content: message.value }],
  stream: stream.value,
}));
const codeMode = ref("curl");
const example = computed(() =>
  requestExample(window.location.origin, body.value, codeMode.value),
);

async function copy() {
  try {
    await copyText(example.value);
    notify("示例已复制", "success");
  } catch {
    notify("复制失败", "error");
  }
}
const labels = {
  idle: "等待请求",
  running: "生成中",
  success: "已完成",
  error: "调用失败",
  cancelled: "已停止",
};
</script>
<template>
  <section class="view-stack">
    <PageHeader
      title="API 测试"
      eyebrow="PLAYGROUND"
      description="验证模型接口，直接阅读回复与诊断信息。"
    />
    <ResourceNotice :error="modelError" @retry="refresh" />
    <div class="api-test-grid">
      <section class="data-section form-section">
        <header><h3>发送请求</h3></header>
        <form class="view-stack" @submit.prevent="send(body)">
          <label
            >模型<select v-model="model" :disabled="status === 'running'">
              <option v-if="!models.length" value="">暂无可用模型</option>
              <option v-for="item in models" :key="item.id">
                {{ item.id }}
              </option>
            </select></label
          ><label
            >消息<textarea
              v-model="message"
              rows="9"
              :disabled="status === 'running'"
            /></label
          ><label class="switch-row"
            ><span>流式响应<small>边生成边显示回复</small></span
            ><input
              v-model="stream"
              type="checkbox"
              :disabled="status === 'running'"
          /></label>
          <p class="notice">
            <strong>此操作会调用真实模型并可能消耗积分。</strong
            >停止接收后，已发送的上游请求仍可能计费。
          </p>
          <button
            v-if="status !== 'running'"
            class="button primary"
            :disabled="!model || !message.trim()"
          >
            <Play :size="16" />发送请求</button
          ><button
            v-else
            type="button"
            class="button secondary"
            @click="cancel"
          >
            <Square :size="16" />停止接收
          </button>
        </form>
      </section>
      <section class="data-section result-section">
        <header>
          <h3>模型回复</h3>
          <StatusBadge
            :state="status === 'running' ? 'pending' : status"
            :label="labels[status]"
          />
        </header>
        <div v-if="error" class="notice error" role="alert">{{ error }}</div>
        <details v-if="reasoning">
          <summary>思考过程</summary>
          <pre class="reply-content">{{ reasoning }}</pre>
        </details>
        <pre class="reply-content" :class="{ placeholder: !content }">{{
          content ||
          (raw
            ? "模型未返回文本内容，可查看原始响应中的工具调用等字段。"
            : "发送一条消息，回复会显示在这里。")
        }}</pre>
        <footer>
          <span class="muted">{{
            duration !== null
              ? `总耗时 ${latency(duration)}`
              : "不会自动重发失败请求"
          }}</span
          ><button class="text-button" @click="rawVisible = !rawVisible">
            {{ rawVisible ? "收起原始响应" : "查看原始响应" }}
          </button>
        </footer>
        <pre v-if="rawVisible" class="raw-response">{{
          raw || "暂无响应"
        }}</pre>
      </section>
    </div>
    <section class="code-section">
      <header>
        <div class="segmented-control" aria-label="示例语言">
          <button
            :class="{ active: codeMode === 'curl' }"
            @click="codeMode = 'curl'"
          >
            cURL</button
          ><button
            :class="{ active: codeMode === 'python' }"
            @click="codeMode = 'python'"
          >
            Python
          </button>
        </div>
        <button class="icon-button" aria-label="复制示例代码" @click="copy">
          <Copy :size="17" />
        </button>
      </header>
      <pre>{{ example }}</pre>
    </section>
  </section>
</template>

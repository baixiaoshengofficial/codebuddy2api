<script setup>
import { Copy } from "@lucide/vue";
import AppDrawer from "../AppDrawer.vue";
import ResourceNotice from "../ResourceNotice.vue";
import StatusBadge from "../StatusBadge.vue";
import { consoleApi } from "../../services/console";
import { useResource } from "../../composables/useResource";
import { copyText } from "../../lib/api";
import { notify } from "../../lib/notify";
import { dateTime, latency } from "../../lib/format";
const props = defineProps({ id: Number });
defineEmits(["close"]);
const { data, loading, error, refresh } = useResource(
  () => consoleApi.request(props.id),
  null,
  5000,
);
async function copyDiagnostics() {
  const {
    request_id,
    endpoint,
    model,
    status,
    status_code,
    upstream_attempts,
    error,
    attempts,
  } = data.value;
  try {
    await copyText(
      JSON.stringify(
        {
          request_id,
          endpoint,
          model,
          status,
          status_code,
          upstream_attempts,
          error,
          attempts,
        },
        null,
        2,
      ),
    );
    notify("诊断信息已复制", "success");
  } catch {
    notify("复制失败，请手动复制", "error");
  }
}
</script>
<template>
  <AppDrawer
    title="请求详情"
    :subtitle="data?.request_id || `记录 #${id}`"
    @close="$emit('close')"
  >
    <ResourceNotice :error="error" @retry="refresh" />
    <div v-if="loading && !data" class="empty-state">正在加载详情…</div>
    <template v-if="data">
      <div class="toolbar-actions">
        <StatusBadge :state="data.status" /><span class="muted"
          >{{ dateTime(data.created_at) }} ·
          {{ latency(data.latency_ms) }}</span
        ><button class="button secondary" @click="copyDiagnostics">
          <Copy :size="15" />复制诊断
        </button>
      </div>
      <dl class="detail-grid">
        <div>
          <dt>模型</dt>
          <dd>{{ data.model }}</dd>
        </div>
        <div>
          <dt>来源</dt>
          <dd>{{ data.client }}</dd>
        </div>
        <div>
          <dt>最终凭证</dt>
          <dd>{{ data.credential || "—" }}</dd>
        </div>
        <div>
          <dt>HTTP 状态</dt>
          <dd>{{ data.status_code || "等待完成" }}</dd>
        </div>
        <div>
          <dt>Tokens {{ data.token_estimated ? "（估算）" : "" }}</dt>
          <dd>{{ data.total_tokens ?? "—" }}</dd>
        </div>
        <div>
          <dt>上游尝试</dt>
          <dd>{{ data.upstream_attempts }} 次</dd>
        </div>
      </dl>
      <div v-if="data.error" class="notice error" role="alert">
        {{ data.error }}
      </div>
      <section>
        <h3>上游请求过程</h3>
        <p class="section-description">
          每次尝试代表一次上游请求，不代表已经确认扣费。
        </p>
        <ol v-if="data.attempts.length" class="timeline attempt-timeline">
          <li v-for="attempt in data.attempts" :key="attempt.id">
            <i /><strong
              >第 {{ attempt.number }} 次 ·
              {{ attempt.credential || "未知凭证" }}</strong
            >
            <p>
              <StatusBadge
                :state="
                  !attempt.completed_at
                    ? 'pending'
                    : attempt.error || attempt.status_code >= 400
                      ? 'error'
                      : 'success'
                "
              />
              HTTP {{ attempt.status_code || "—" }} ·
              {{
                latency(
                  attempt.completed_at
                    ? attempt.completed_at - attempt.started_at
                    : null,
                )
              }}
            </p>
            <p v-if="attempt.error" class="error-text">{{ attempt.error }}</p>
            <small>{{ dateTime(attempt.started_at) }}</small>
          </li>
        </ol>
        <p v-else class="notice">
          该记录未保存逐次尝试明细。历史记录无法补出切换过程。
        </p>
      </section>
      <section>
        <h3>本轮输入预览</h3>
        <pre class="message-preview">{{ data.request_preview || "—" }}</pre>
      </section>
      <section>
        <h3>回复预览</h3>
        <pre class="message-preview">{{
          data.response_preview || "未记录回复内容"
        }}</pre>
      </section>
      <p class="muted">预览经过脱敏与截断，不是完整对话。</p>
    </template>
  </AppDrawer>
</template>

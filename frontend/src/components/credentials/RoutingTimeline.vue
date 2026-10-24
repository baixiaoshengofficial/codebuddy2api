<script setup>
import { computed, ref } from "vue";
import { Route, ShieldCheck } from "@lucide/vue";
import { dateTime, eventLabels } from "../../lib/format";
const props = defineProps({ events: Array, cooldownSeconds: Number });
defineEmits(["request"]);
const expanded = ref(false);
const visibleEvents = computed(() =>
  expanded.value ? props.events : props.events.slice(0, 5),
);
</script>
<template>
  <aside class="view-stack routing-sidebar">
    <section class="data-section">
      <header>
        <h3>最近切换</h3>
        <Route :size="17" />
      </header>
      <div v-if="!events.length" class="empty-state">暂无切换记录</div>
      <ol v-else class="timeline">
        <li v-for="event in visibleEvents" :key="event.id">
          <i /><strong>{{ eventLabels[event.reason] || event.reason }}</strong>
          <p>
            {{ event.previous || "未选择" }} →
            {{ event.current || "无可用凭证" }}
          </p>
          <small>{{ dateTime(event.created_at) }}</small>
          <p v-if="event.request_record_id">
            <button
              class="text-button"
              @click="$emit('request', event.request_record_id)"
            >
              查看关联请求 →
            </button>
          </p>
        </li>
      </ol>
      <div v-if="events.length > 5" class="table-toolbar">
        <button class="text-button" @click="expanded = !expanded">
          {{ expanded ? "收起记录" : "展开最近 30 条记录" }}
        </button>
      </div>
    </section>
    <section class="data-section rules">
      <header>
        <h3>切换规则</h3>
        <ShieldCheck :size="17" />
      </header>
      <ol>
        <li>持续使用当前可用凭证。</li>
        <li>上游明确返回积分不足时，跳过该凭证 {{ cooldownSeconds }} 秒。</li>
        <li>响应尚未输出时，可转到下一个凭证。</li>
        <li>网络异常不会自动重发，避免重复消耗。</li>
      </ol>
      <p class="muted">
        可用状态依据本地有效期与冷却记录判断，剩余积分以官方后台为准。
      </p>
    </section>
  </aside>
</template>

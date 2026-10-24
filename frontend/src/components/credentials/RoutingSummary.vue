<script setup>
import { computed } from "vue";
import { ArrowRight, Route, ShieldCheck } from "@lucide/vue";
import { credentialLabel } from "../../lib/format";
const props = defineProps({ routing: Object, busy: Boolean });
defineEmits(["mode"]);
const current = computed(() =>
  props.routing.credentials.find(
    (row) => row.index === props.routing.current_index,
  ),
);
const next = computed(() =>
  props.routing.credentials.find(
    (row) => row.index === props.routing.next_index,
  ),
);
</script>
<template>
  <section class="routing-summary">
    <header>
      <span class="eyebrow"><Route :size="15" />凭证路由</span
      ><span class="status-badge available">{{
        routing.mode === "manual" ? "手动锁定" : "自动切换已开启"
      }}</span>
    </header>
    <div class="routing-path">
      <div>
        <small>当前使用</small><strong>{{ credentialLabel(current) }}</strong
        ><span>{{ current?.filename || "添加凭证后即可开始" }}</span>
      </div>
      <ArrowRight :size="23" />
      <div>
        <small>{{
          routing.mode === "manual" ? "切换策略" : "下一个可用凭证"
        }}</small
        ><strong>{{
          routing.mode === "manual"
            ? "保持手动锁定"
            : next
              ? credentialLabel(next)
              : "暂无备用凭证"
        }}</strong
        ><span>{{
          routing.mode === "manual"
            ? "由你选择使用的账号"
            : "当前凭证积分不足时才切换"
        }}</span>
      </div>
      <button
        class="button secondary"
        :disabled="busy || (routing.mode !== 'manual' && !current)"
        @click="
          $emit(
            'mode',
            routing.mode === 'manual' ? 'auto' : 'manual',
            current?.filename,
          )
        "
      >
        {{ routing.mode === "manual" ? "恢复自动切换" : "锁定当前凭证" }}
      </button>
    </div>
    <footer>
      <ShieldCheck
        :size="15"
      />网络错误不会自动重发；回复已开始生成时，切换只对后续请求生效。
    </footer>
  </section>
</template>

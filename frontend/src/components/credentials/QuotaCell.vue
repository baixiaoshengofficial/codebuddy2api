<script setup>
import { ChevronDown, RefreshCw } from "@lucide/vue";
import { dateTime } from "../../lib/format";
import { creditAmount, quotaLabel } from "../../lib/quota";
defineProps({
  result: Object,
  loading: Boolean,
  busy: Boolean,
  label: String,
  error: String,
});
defineEmits(["refresh"]);
</script>
<template>
  <div class="quota-cell" :aria-busy="busy || loading">
    <div class="quota-value">
      <strong
        :class="{ 'quota-stale': result?.stale }"
        :title="result?.balance?.remaining"
      >
        {{ quotaLabel(result, loading)
        }}<span v-if="result?.balance"> 积分</span>
      </strong>
      <button
        class="icon-button compact"
        :disabled="busy || loading"
        :aria-label="`刷新 ${label} 的额度`"
        title="查询剩余额度，不调用模型"
        @click="$emit('refresh')"
      >
        <RefreshCw :size="14" :class="{ spin: busy || loading }" />
      </button>
    </div>
    <small
      v-if="result?.status && result.status !== 'ok'"
      class="quota-message"
      role="status"
    >
      {{ result.stale ? "上次结果 · " : "" }}{{ result.message }}
    </small>
    <small v-else-if="error" class="quota-message" role="status">{{
      error
    }}</small>
    <small v-if="result?.updated_at" :title="dateTime(result.updated_at * 1000)"
      >更新于 {{ dateTime(result.updated_at * 1000) }}</small
    >
    <details v-if="result?.balance" class="quota-details">
      <summary title="查看额度组成与到期时间">
        <span>额度明细</span>
        <ChevronDown :size="14" class="quota-chevron" aria-hidden="true" />
      </summary>
      <dl>
        <div>
          <dt>当前额度</dt>
          <dd :title="result.balance.total">
            {{ creditAmount(result.balance.total) }}
          </dd>
        </div>
        <div>
          <dt>已用</dt>
          <dd :title="result.balance.used">
            {{ creditAmount(result.balance.used) }}
          </dd>
        </div>
        <div v-if="result.balance.next_expiry_at" class="quota-expiry">
          <dt>最近到期 / 重置</dt>
          <dd>
            {{ dateTime(result.balance.next_expiry_at * 1000) }}<br />{{
              creditAmount(result.balance.expiring_credits)
            }}
            积分
          </dd>
        </div>
      </dl>
    </details>
  </div>
</template>

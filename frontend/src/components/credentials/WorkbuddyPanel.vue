<script setup>
import { computed } from "vue";
import { Gift, RefreshCw, Trash2 } from "@lucide/vue";
import EmptyState from "../EmptyState.vue";
import ResourceMeta from "../ResourceMeta.vue";
import QuotaCell from "./QuotaCell.vue";
import { useQuotas } from "../../composables/useQuotas";
import AuthorizationPanel from "./AuthorizationPanel.vue";
import ResourceNotice from "../ResourceNotice.vue";
import StatusBadge from "../StatusBadge.vue";
import { consoleApi } from "../../services/console";
import { useResource } from "../../composables/useResource";
import { useAction } from "../../composables/useAction";
import { dateTime } from "../../lib/format";
import { workbuddyAuthorization } from "../../lib/credentialState";
const quotas = useQuotas("workbuddy");
async function refreshAccounts() {
  await Promise.all([refresh(), quotas.refresh()]);
}
const { data, loading, error, updatedAt, refresh } = useResource(
  async () => {
    const [credentials, checkin] = await Promise.all([
      consoleApi.workbuddy(),
      consoleApi.checkin(),
    ]);
    return { credentials, checkin };
  },
  { credentials: [], checkin: {} },
  30000,
);
const { busy, run } = useAction();
const rows = computed(() =>
  data.value.credentials.map((row) => {
    const checkin =
      data.value.checkin.accounts?.find(
        (item) => item.filename === row.filename,
      ) || {};
    return {
      ...row,
      checkin,
      authorization: workbuddyAuthorization(row, checkin),
    };
  }),
);
const labels = {
  claimed: "领取成功",
  already_checked_in: "今日已签",
  inactive: "活动未开放",
  expired: "已过期",
  error: "检查失败",
  pending: "等待检查",
};
async function remove(row) {
  if (
    !window.confirm(
      `删除 WorkBuddy 账号 ${row.phone || row.email || row.filename}？`,
    )
  )
    return;
  await run(async () => {
    await consoleApi.deleteWorkbuddy(row.filename);
    await refreshAccounts();
  }, "已删除 WorkBuddy 凭证");
}
</script>
<template>
  <ResourceNotice :error="error" @retry="refresh" />
  <section class="data-section">
    <header>
      <div>
        <h3>WorkBuddy 签到账号</h3>
        <small>{{
          data.checkin.enabled
            ? `每天 ${data.checkin.schedule_time || "11:00"}（北京时间）自动签到`
            : "自动签到已关闭"
        }}</small>
      </div>
      <div class="toolbar-actions">
        <button
          class="button secondary"
          :disabled="busy || data.checkin.running || !rows.length"
          @click="
            run(async () => {
              await consoleApi.runCheckin();
              await refreshAccounts();
            }, '签到检查完成')
          "
        >
          <Gift :size="16" />立即签到</button
        ><button
          class="icon-button"
          :disabled="loading"
          title="刷新账号、签到状态与额度"
          @click="refreshAccounts"
        >
          <RefreshCw :size="17" :class="{ spin: loading }" /></button
        ><AuthorizationPanel product="workbuddy" @saved="refreshAccounts" />
      </div>
    </header>
    <p class="section-description">
      这些账号用于签到，不参与 CodeBuddy 模型请求的凭证切换。额度缓存 5
      分钟；同一账号在两种产品下的额度可能共享。
    </p>
    <EmptyState
      v-if="!rows.length"
      :icon="Gift"
      :loading="loading"
      :title="
        loading
          ? '正在加载签到账号…'
          : error
            ? '签到账号加载失败'
            : '暂无 WorkBuddy 账号'
      "
      :description="error || '添加 WorkBuddy 账号后即可签到和查询额度。'"
    />
    <div v-for="row in rows" :key="row.filename" class="workbuddy-row">
      <div class="avatar"><Gift :size="18" /></div>
      <div class="workbuddy-account">
        <strong>{{
          row.phone || row.email || row.user_id || row.filename
        }}</strong>
        <small>{{ row.filename }}</small>
      </div>
      <div class="workbuddy-auth-state">
        <small class="field-caption">授权状态</small>
        <StatusBadge
          :state="row.authorization.state"
          :label="row.authorization.label"
        />
        <small>{{
          row.expires_at
            ? `到期 ${dateTime(row.expires_at * 1000)}`
            : "未提供到期时间"
        }}</small>
      </div>
      <div class="workbuddy-quota">
        <small class="field-caption">剩余额度</small>
        <QuotaCell
          :result="
            quotas.data.value.accounts.find(
              (item) => item.filename === row.filename,
            )
          "
          :loading="quotas.loading.value"
          :error="quotas.error.value"
          :busy="quotas.refreshing.value.has(row.filename)"
          :label="row.phone || row.email || row.filename"
          @refresh="quotas.refreshOne(row.filename)"
        />
      </div>
      <div class="workbuddy-checkin-state">
        <small class="field-caption">签到结果</small>
        <StatusBadge
          :state="
            ['claimed', 'already_checked_in'].includes(row.checkin.status)
              ? 'success'
              : row.checkin.status || 'pending'
          "
          :label="labels[row.checkin.status] || '等待检查'"
        />
        <small :title="row.checkin.message">{{
          row.checkin.checked_at
            ? dateTime(row.checkin.checked_at * 1000)
            : "尚未执行"
        }}</small>
      </div>
      <div class="row-actions workbuddy-actions">
        <AuthorizationPanel
          v-if="
            row.is_expired ||
            row.checkin.status === 'expired' ||
            quotas.data.value.accounts.find(
              (item) => item.filename === row.filename,
            )?.status === 'unauthorized'
          "
          product="workbuddy"
          :credential="row"
          :disabled="busy"
          @saved="refreshAccounts"
        />
        <button
          class="icon-button danger"
          :disabled="busy"
          title="删除 WorkBuddy 账号"
          :aria-label="`删除 WorkBuddy 账号 ${row.phone || row.email || row.filename}`"
          @click="remove(row)"
        >
          <Trash2 :size="16" />
        </button>
      </div>
    </div>
    <ResourceMeta
      :updated-at="updatedAt"
      :loading="loading"
      label="账号状态每 30 秒更新 · 额度缓存 5 分钟"
    />
  </section>
</template>

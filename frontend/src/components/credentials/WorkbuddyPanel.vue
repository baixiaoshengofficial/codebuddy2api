<script setup>
import { computed } from "vue";
import { Gift, RefreshCw, Trash2 } from "@lucide/vue";
import AuthorizationPanel from "./AuthorizationPanel.vue";
import ResourceNotice from "../ResourceNotice.vue";
import StatusBadge from "../StatusBadge.vue";
import { consoleApi } from "../../services/console";
import { useResource } from "../../composables/useResource";
import { useAction } from "../../composables/useAction";
import { dateTime } from "../../lib/format";
const { data, loading, error, refresh } = useResource(
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
  data.value.credentials.map((row, index) => ({
    ...row,
    index,
    checkin:
      data.value.checkin.accounts?.find(
        (item) => item.filename === row.filename,
      ) || {},
  })),
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
    await consoleApi.deleteWorkbuddy(row.index);
    await refresh();
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
              await refresh();
            }, '签到检查完成')
          "
        >
          <Gift :size="16" />立即签到</button
        ><button
          class="icon-button"
          :disabled="loading"
          title="刷新签到状态"
          @click="refresh"
        >
          <RefreshCw :size="17" :class="{ spin: loading }" /></button
        ><AuthorizationPanel product="workbuddy" @saved="refresh" />
      </div>
    </header>
    <p class="section-description">
      这些账号用于签到，不参与 CodeBuddy 模型请求的凭证切换。
    </p>
    <div v-if="!rows.length" class="empty-state">
      {{ data.checkin.message || "添加 WorkBuddy 账号后即可签到" }}
    </div>
    <div v-for="row in rows" :key="row.filename" class="workbuddy-row">
      <div class="avatar"><Gift :size="18" /></div>
      <div class="grow">
        <strong>{{
          row.phone || row.email || row.user_id || row.filename
        }}</strong
        ><small>{{ row.checkin.message || row.filename }}</small>
      </div>
      <div>
        <StatusBadge
          :state="
            ['claimed', 'already_checked_in'].includes(row.checkin.status)
              ? 'success'
              : row.checkin.status || 'pending'
          "
          :label="labels[row.checkin.status] || '等待检查'"
        /><small>{{
          row.checkin.checked_at
            ? dateTime(row.checkin.checked_at * 1000)
            : "尚未执行"
        }}</small>
      </div>
      <button
        class="icon-button danger"
        :disabled="busy"
        title="删除 WorkBuddy 账号"
        @click="remove(row)"
      >
        <Trash2 :size="16" />
      </button>
    </div>
  </section>
</template>

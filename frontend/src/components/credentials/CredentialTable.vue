<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { KeyRound, LockKeyhole, Trash2, ShieldCheck } from "@lucide/vue";
import StatusBadge from "../StatusBadge.vue";
import AuthorizationPanel from "./AuthorizationPanel.vue";
import { credentialLabel, dateTime } from "../../lib/format";
const props = defineProps({
  rows: Array,
  busy: Boolean,
  serverTime: Number,
  filtered: Boolean,
});
defineEmits(["lock", "delete", "test", "saved", "reset"]);
const now = ref(Date.now());
let timer,
  offset = 0;
onMounted(() => {
  offset = (props.serverTime || Date.now()) - Date.now();
  timer = setInterval(() => {
    now.value = Date.now() + offset;
  }, 1000);
});
onBeforeUnmount(() => clearInterval(timer));
function cooldown(row) {
  const seconds = Math.max(
    0,
    Math.ceil((row.cooldown_until - now.value) / 1000),
  );
  return seconds
    ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} 后重新候选`
    : "等待状态刷新";
}
</script>
<template>
  <div class="table-shell">
    <table class="data-table credential-table">
      <thead>
        <tr>
          <th>账号</th>
          <th>状态</th>
          <th>有效期</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!rows.length">
          <td colspan="4">
            <div class="empty-state">
              <KeyRound :size="24" /><strong>{{
                filtered
                  ? "没有符合条件的凭证"
                  : "暂无凭证，点击“添加凭证”完成授权"
              }}</strong>
              <button
                v-if="filtered"
                class="button secondary"
                @click="$emit('reset')"
              >
                重置筛选
              </button>
            </div>
          </td>
        </tr>
        <tr
          v-for="row in rows"
          :key="row.filename"
          :class="{ selected: row.is_current }"
        >
          <td data-label="账号">
            <div class="account-cell">
              <span class="avatar">{{
                credentialLabel(row).slice(0, 2).toUpperCase()
              }}</span>
              <div>
                <strong>{{ credentialLabel(row) }}</strong
                ><small>{{ row.filename }}</small>
              </div>
            </div>
          </td>
          <td data-label="状态">
            <StatusBadge
              :state="row.is_current ? 'active' : row.state"
              :label="row.is_current ? '当前使用' : undefined"
            /><StatusBadge
              v-if="row.is_current && row.state === 'refreshable'"
              state="refreshable"
            /><small v-if="row.state === 'cooldown'">{{ cooldown(row) }}</small>
          </td>
          <td data-label="有效期">
            <strong>{{
              row.expires_at
                ? dateTime(row.expires_at * 1000)
                : "未提供到期时间"
            }}</strong
            ><small>{{
              row.state === "reauthorize"
                ? "登录会话已失效，请重新授权"
                : row.has_refresh_token
                  ? "支持自动续期"
                  : "到期后需要重新授权"
            }}</small>
          </td>
          <td data-label="操作">
            <div class="row-actions">
              <AuthorizationPanel
                v-if="
                  row.is_expired ||
                  ['expired', 'reauthorize'].includes(row.state)
                "
                product="codebuddy"
                :credential="row"
                :disabled="busy"
                @saved="$emit('saved')"
              />
              <button
                class="icon-button compact"
                :disabled="
                  busy || ['expired', 'reauthorize'].includes(row.state)
                "
                :aria-label="`锁定 ${credentialLabel(row)}`"
                title="手动锁定"
                @click="$emit('lock', row)"
              >
                <LockKeyhole :size="16" /></button
              ><button
                class="icon-button compact"
                :disabled="busy"
                :aria-label="`测试 ${credentialLabel(row)}`"
                title="模型测试（可能消耗积分）"
                @click="$emit('test', row)"
              >
                <ShieldCheck :size="16" /></button
              ><button
                class="icon-button compact danger"
                :disabled="busy"
                :aria-label="`删除 ${credentialLabel(row)}`"
                title="删除凭证"
                @click="$emit('delete', row)"
              >
                <Trash2 :size="16" />
              </button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

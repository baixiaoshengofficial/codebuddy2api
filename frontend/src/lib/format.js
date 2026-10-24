export const dateTime = (value) =>
  value
    ? new Date(Number(value)).toLocaleString("zh-CN", {
        hour12: false,
        timeZone: "Asia/Shanghai",
      })
    : "—";
export const latency = (value) =>
  value == null
    ? "—"
    : value >= 1000
      ? `${(value / 1000).toFixed(2)} s`
      : `${value} ms`;
export const credentialLabel = (row) =>
  row?.name || row?.email || row?.user_id || row?.filename || "暂无可用凭证";
export const stateLabels = {
  available: "可用",
  refreshable: "可自动续期",
  cooldown: "积分不足 · 冷却中",
  reauthorize: "需要重新授权",
  expired: "已过期",
  success: "成功",
  error: "失败",
  pending: "处理中",
};
export const eventLabels = {
  insufficient_credit: "积分不足，自动切换",
  manual_selection: "手动锁定凭证",
  automatic_mode: "恢复自动模式",
};

export const navigation = [
  { id: "dashboard", label: "运行概览", hint: "请求表现与服务状态" },
  { id: "credentials", label: "凭证管理", hint: "账号授权与剩余额度" },
  { id: "requests", label: "请求明细", hint: "调用记录与上游尝试" },
  { id: "api-test", label: "API 测试", hint: "调试请求与接入示例" },
  { id: "settings", label: "服务设置", hint: "访问方式与自动任务" },
];
export function isView(id) {
  return navigation.some((item) => item.id === id);
}
export function resolveView(hash, stored = "credentials") {
  const id = String(hash || "").replace(/^#/, "");
  return isView(id) ? id : isView(stored) ? stored : "credentials";
}

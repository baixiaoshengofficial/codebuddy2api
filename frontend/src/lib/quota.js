const formatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
export function creditAmount(value) {
  if (value == null || String(value).trim() === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return "—";
  if (number > 0 && number < 0.01) return "<0.01";
  return formatter.format(number);
}
export function quotaLabel(result, loading = false) {
  if (result?.balance) return creditAmount(result.balance.remaining);
  if (loading) return "查询中…";
  return (
    { unauthorized: "需授权", forbidden: "不可查询", unsupported: "暂不支持" }[
      result?.status
    ] || "未知"
  );
}

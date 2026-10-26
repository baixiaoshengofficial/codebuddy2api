export function workbuddyAuthorization(row, checkin = {}) {
  if (checkin.status === "expired")
    return { state: "reauthorize", label: "需要重新授权" };
  if (row.is_expired)
    return row.has_refresh_token
      ? { state: "refreshable", label: "待自动续期" }
      : { state: "expired", label: "已过期" };
  return { state: "available", label: "已授权" };
}

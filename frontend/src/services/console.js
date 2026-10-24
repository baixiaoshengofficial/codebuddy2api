import { apiFetch, authHeaders } from "../lib/api";

const json = (method, body) => ({ method, body: JSON.stringify(body) });
export const consoleApi = {
  status: () => apiFetch("/api/console/status"),
  credentials: () => apiFetch("/api/console/credentials"),
  setMode: (mode, filename) =>
    apiFetch("/api/console/credentials/mode", json("POST", { mode, filename })),
  deleteCredential: (filename) =>
    apiFetch(`/api/console/credentials/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }),
  requests: (params) => apiFetch(`/api/console/requests?${params}`),
  request: (id) => apiFetch(`/api/console/requests/${id}`),
  overview: (params) => apiFetch(`/api/console/overview?${params}`),
  models: async () => (await apiFetch("/codebuddy/v1/models")).data || [],
  settings: () => apiFetch("/api/settings"),
  saveSettings: (settings) =>
    apiFetch("/api/settings", json("POST", { settings })),
  workbuddy: async () =>
    (await apiFetch("/workbuddy/credentials")).credentials || [],
  checkin: () => apiFetch("/api/checkin"),
  runCheckin: () => apiFetch("/api/checkin/run", { method: "POST" }),
  deleteWorkbuddy: (index) =>
    apiFetch("/workbuddy/credentials/delete", json("POST", { index })),
  startAuthorization: (product, signal) =>
    apiFetch(`/${product}/auth/start`, { signal }),
  pollAuthorization: (product, authState, signal) =>
    apiFetch(`/${product}/auth/poll`, {
      ...json("POST", { auth_state: authState }),
      signal,
    }),
};

export async function exportRequests(params) {
  const response = await fetch(`/api/console/requests/export?${params}`, {
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error(`导出失败 HTTP ${response.status}`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = "codebuddy-request-logs.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

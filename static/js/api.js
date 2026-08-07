const STORAGE_KEY = "nexus.dashboard.key";

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export function getApiKey() { return localStorage.getItem(STORAGE_KEY) || ""; }
export function setApiKey(value) { localStorage.setItem(STORAGE_KEY, value.trim()); }

function errorMessage(payload) {
  const detail = payload?.detail ?? payload?.error;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg || item?.message).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  if (detail && typeof detail === "object") {
    return detail.message || detail.msg || "Falha na API";
  }
  return "Falha na API";
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 204) return null;
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new ApiError(errorMessage(payload), response.status);
  return payload.data ?? payload;
}

export const api = {
  accounts: () => request("/api/v1/accounts"),
  bots: () => request("/api/v1/bots"),
  bot: (id) => request(`/api/v1/bots/${id}`),
  createBot: (data) => request("/api/v1/bots", { method: "POST", body: JSON.stringify(data) }),
  updateBot: (id, data) => request(`/api/v1/bots/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  realConfirmation: (id, phrase) => request(`/api/v1/bots/${id}/real-confirmation`, { method: "POST", body: JSON.stringify({ phrase }) }),
  startBot: (id, realTicket = "") => request(`/api/v1/bots/${id}/start`, { method: "POST", body: JSON.stringify(realTicket ? { real_ticket: realTicket } : {}) }),
  stopBot: (id) => request(`/api/v1/bots/${id}/stop`, { method: "POST" }),
  stopAll: () => request("/api/v1/bots/stop-all", { method: "POST" }),
  trades: (id) => request(`/api/v1/bots/${id}/trades?limit=100`),
  snapshot: (id) => request(`/api/v1/bots/${id}/snapshot`),
  wsTicket: (id) => request(`/api/v1/ws-tickets/${id}`, { method: "POST" }),
};

export function websocketUrl(botId, ticket) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/api/v1/ws/bots/${encodeURIComponent(botId)}?ticket=${encodeURIComponent(ticket)}`;
}

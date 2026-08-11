import { ApiError, getApiKey } from "./api.js";

const BASE = "/api/v1/nexus-trade";

function readableError(payload) {
  const detail = payload?.detail ?? payload?.error;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg || item?.message).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  if (detail && typeof detail === "object") return detail.message || detail.msg || "Falha na API NexusTrade";
  return "Falha na API NexusTrade";
}

function safeSegment(value, label) {
  const normalized = String(value ?? "").trim();
  if (!normalized) throw new ApiError(`${label} obrigatório`, 422);
  return encodeURIComponent(normalized);
}

function headers(humanKey = "", json = true) {
  const value = {};
  if (json) value["Content-Type"] = "application/json";
  const dashboardKey = getApiKey();
  if (dashboardKey) value["X-API-Key"] = dashboardKey;
  if (humanKey) value["X-Nexus-Human-Key"] = humanKey;
  return value;
}

async function parseFailure(response) {
  let payload = {};
  try { payload = await response.json(); } catch { /* non-JSON error */ }
  throw new ApiError(readableError(payload), response.status);
}

function filenameFrom(response, fallback) {
  const disposition = response.headers?.get?.("content-disposition") || "";
  const match = disposition.match(/filename\*?=(?:UTF-8''|["']?)([^"';\r\n]+)/i);
  const candidate = decodeURIComponent((match?.[1] || fallback).trim());
  return candidate.split(/[\\/]/).pop() || fallback;
}

export function createNexusTradeApi(fetchImpl = (...args) => globalThis.fetch(...args)) {
  const request = async (path, { method = "GET", body, humanKey = "" } = {}) => {
    const response = await fetchImpl(path, {
      method,
      headers: headers(humanKey),
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) return parseFailure(response);
    if (response.status === 204) return null;
    const payload = await response.json();
    return payload?.data ?? payload;
  };

  const governance = (path, payload, humanKey) => {
    if (!String(humanKey || "").trim()) throw new ApiError("Credencial humana obrigatória", 403);
    return request(path, { method: "POST", body: payload, humanKey });
  };

  return {
    snapshot: () => request(BASE),
    setMode: (payload) => request(`${BASE}/mode`, { method: "POST", body: payload }),
    confirmReal: (accountId, phrase) => request(`${BASE}/real-confirmation`, {
      method: "POST",
      body: { account_id: accountId, phrase },
    }),
    emergencyStop: (enabled = true) => request(`${BASE}/emergency-stop`, {
      method: "POST",
      body: { enabled: Boolean(enabled) },
    }),
    versions: () => request(`${BASE}/versions`),
    campaigns: () => request(`${BASE}/campaigns`),
    reports: () => request(`${BASE}/reports`),
    weeklyReport: (weekStart) => request(`${BASE}/reports/weekly/${safeSegment(weekStart, "Semana")}`),
    report: (reportId) => request(`${BASE}/reports/${safeSegment(reportId, "Relatório")}`),
    proposals: () => request(`${BASE}/proposals`),
    exports: () => request(`${BASE}/exports`),
    approve: (proposalId, payload, humanKey) => governance(
      `${BASE}/proposals/${safeSegment(proposalId, "Proposta")}/approve`, payload, humanKey,
    ),
    reanalyze: (proposalId, payload, humanKey) => governance(
      `${BASE}/proposals/${safeSegment(proposalId, "Proposta")}/reanalyze`, payload, humanKey,
    ),
    rollback: (payload, humanKey) => governance(`${BASE}/rollback`, payload, humanKey),
    async downloadReport(reportId, formatName) {
      const format = String(formatName || "").toLowerCase();
      if (!["csv.zip", "xlsx"].includes(format)) throw new ApiError("Formato de exportação inválido", 422);
      const safeId = safeSegment(reportId, "Relatório");
      const response = await fetchImpl(`${BASE}/reports/${safeId}/exports/${format}`, {
        method: "GET",
        headers: headers("", false),
      });
      if (!response.ok) return parseFailure(response);
      const mediaType = response.headers?.get?.("content-type") || "application/octet-stream";
      const buffer = await response.arrayBuffer();
      if (!buffer?.byteLength) throw new ApiError("O arquivo de exportação veio vazio", 502);
      return {
        filename: filenameFrom(response, `nexustrade-${reportId}.${format}`),
        mediaType,
        blob: new Blob([buffer], { type: mediaType }),
      };
    },
  };
}

export const nexusTradeApi = createNexusTradeApi();

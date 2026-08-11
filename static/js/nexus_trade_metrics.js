const decimal = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ratio = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const integer = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const VALID_TABS = new Set(["operations", "reports", "evolution"]);

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function formatMoney(value) {
  const number = finite(value);
  if (number === null) return "—";
  return `${number < 0 ? "-" : ""}US$ ${decimal.format(Math.abs(number))}`;
}

export function formatPercent(value) {
  const number = finite(value);
  return number === null ? "—" : `${decimal.format(number * 100)}%`;
}

export function formatRatio(value) {
  const number = finite(value);
  return number === null ? "—" : ratio.format(number);
}

export function formatAccuracy(metrics = {}) {
  const wins = finite(metrics.wins);
  const losses = finite(metrics.losses);
  const decisive = finite(metrics.n_decisive) ?? ((wins ?? 0) + (losses ?? 0));
  const accuracy = finite(metrics.accuracy) ?? (decisive > 0 && wins !== null ? wins / decisive : null);
  return `${integer.format(wins ?? 0)}/${integer.format(decisive)} (${formatPercent(accuracy)})`;
}

function formatLane(metrics = {}) {
  const total = finite(metrics.n_total) ?? 0;
  const decisive = finite(metrics.n_decisive) ?? 0;
  const averagePayout = finite(metrics.average_payout);
  return {
    nTotal: integer.format(total),
    nDecisive: integer.format(decisive),
    wins: integer.format(finite(metrics.wins) ?? 0),
    losses: integer.format(finite(metrics.losses) ?? 0),
    ties: integer.format(finite(metrics.ties) ?? 0),
    accuracy: formatAccuracy(metrics),
    breakeven: averagePayout && averagePayout > 0 ? formatPercent(1 / averagePayout) : "—",
    payout: formatRatio(averagePayout),
    capitalAtRisk: formatMoney(metrics.capital_at_risk ?? metrics.total_stake),
    totalPayout: formatMoney(metrics.total_payout),
    profit: formatMoney(metrics.total_profit),
    roi: formatPercent(metrics.normalized_expectancy),
    expectancy: formatPercent(metrics.normalized_expectancy),
    profitFactor: formatRatio(metrics.profit_factor),
    drawdown: formatMoney(metrics.max_drawdown),
    drawdownNormalized: formatPercent(metrics.max_drawdown_normalized),
    recovery: formatRatio(metrics.recovery_factor),
    worstBlock: formatMoney(metrics.worst_rolling_50),
    worstBlockNormalized: formatPercent(metrics.worst_rolling_50_normalized),
    lossStreak: integer.format(finite(metrics.max_loss_streak) ?? 0),
  };
}

function unwrapReport(report) {
  if (!report || typeof report !== "object") return null;
  const snapshot = report.snapshot && typeof report.snapshot === "object" ? report.snapshot : report;
  return { id: String(report.id || snapshot.id || ""), hash: String(report.report_hash || snapshot.report_hash || ""), snapshot };
}

export function buildReportPresentation(report) {
  const wrapped = unwrapReport(report);
  if (!wrapped) return null;
  const snapshot = wrapped.snapshot;
  const totals = snapshot.full_totals || {};
  const progress = snapshot.accumulated_progress || {};
  const operations = finite(progress.operations) ?? 0;
  const target = finite(progress.target) ?? 300;
  const completeDays = finite(progress.complete_days) ?? 0;
  const requiredDays = finite(progress.required_days) ?? 7;
  const endLocal = String(snapshot.window?.end_local || snapshot.window?.end_utc || "");
  return {
    id: wrapped.id,
    hash: wrapped.hash,
    type: String(snapshot.report_type || ""),
    campaignId: String(snapshot.campaign_id || ""),
    week: /^\d{4}-\d{2}-\d{2}/.test(endLocal) ? endLocal.slice(0, 10) : null,
    window: snapshot.window || {},
    versions: {
      champion: String(snapshot.champion?.version_id || "—"),
      trial: String(snapshot.trial?.version_id || "—"),
    },
    days: Array.isArray(snapshot.days) ? snapshot.days.map((day) => ({
      date: String(day?.date || ""),
      champion: formatLane(day?.champion),
      trial: formatLane(day?.trial),
    })) : [],
    weekTotal: {
      champion: formatLane(totals.champion),
      trial: formatLane(totals.trial),
    },
    progress: {
      operations,
      target,
      completeDays,
      requiredDays,
      percent: target > 0 ? Math.min(100, Math.max(0, operations / target * 100)) : 0,
      label: `${integer.format(operations)}/${integer.format(target)} operações · ${integer.format(completeDays)}/${integer.format(requiredDays)} dias`,
      eligible: progress.eligible_count === true && progress.eligible_days === true,
    },
    gates: Array.isArray(snapshot.gates) ? snapshot.gates.map((gate) => ({
      code: String(gate?.code || "—"),
      status: String(gate?.status || "INCONCLUSIVE"),
      observed: gate?.observed ?? null,
      threshold: gate?.threshold ?? null,
      reason: String(gate?.reason || ""),
    })) : [],
    recommendation: String(snapshot.recommendation || "INCONCLUSIVE"),
    recommendationReasons: Array.isArray(snapshot.recommendation_reasons) ? snapshot.recommendation_reasons.map(String) : [],
    diffs: Array.isArray(snapshot.diffs) ? snapshot.diffs : [],
    audit: Array.isArray(snapshot.audit) ? snapshot.audit : [],
    disclosure: String(snapshot.disclosure || ""),
  };
}

function alignedMonday(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  const date = new Date(`${value}T12:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value && date.getUTCDay() === 1;
}

export function shiftAlignedWeek(value, amount) {
  if (!alignedMonday(value) || !Number.isInteger(amount)) return null;
  const date = new Date(`${value}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + amount * 7);
  return date.toISOString().slice(0, 10);
}

export function normalizeReportLocation(search = "") {
  const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  const tab = VALID_TABS.has(params.get("nexus_tab")) ? params.get("nexus_tab") : "operations";
  const week = alignedMonday(params.get("nexus_week")) ? params.get("nexus_week") : null;
  return { tab, week };
}

export function reportLocationSearch(search, { tab, week }) {
  const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  if (VALID_TABS.has(tab) && tab !== "operations") params.set("nexus_tab", tab);
  else params.delete("nexus_tab");
  if (alignedMonday(week)) params.set("nexus_week", week);
  else params.delete("nexus_week");
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export { alignedMonday as isAlignedReportWeek };

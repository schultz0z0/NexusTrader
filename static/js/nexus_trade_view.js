import { NEXUS_BOT_ID } from "./nexus_trade_store.js";
import {
  buildReportPresentation,
  normalizeReportLocation,
  reportLocationSearch,
  shiftAlignedWeek,
} from "./nexus_trade_metrics.js";
import {
  buildDiff,
  buildGovernancePayload,
  evaluatePromotionReadiness,
} from "./nexus_trade_diff.js";

export function resolveDashboardView(botId) {
  return botId === NEXUS_BOT_ID ? "nexus" : "standard";
}

export function saveNexusDownload(download, documentRef = globalThis.document, urlRef = globalThis.URL) {
  if (!download?.blob || typeof download.blob.size !== "number" || download.blob.size < 1) {
    throw new Error("O arquivo de exportação está vazio.");
  }
  const filename = String(download.filename || "").split(/[\\/]/).pop();
  if (!filename) throw new Error("Nome do arquivo de exportação inválido.");
  const anchor = documentRef?.createElement?.("a");
  if (!anchor || !urlRef?.createObjectURL || !urlRef?.revokeObjectURL) {
    throw new Error("Download indisponível neste navegador.");
  }
  const objectUrl = urlRef.createObjectURL(download.blob);
  try {
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.rel = "noopener";
    documentRef.body?.append?.(anchor);
    anchor.click();
    return true;
  } finally {
    anchor.remove?.();
    urlRef.revokeObjectURL(objectUrl);
  }
}

function lane(state, name) {
  return (state?.lanes || []).find((item) => item?.lane === name) || {};
}

function versionLabel(value, fallback) {
  const version = value?.version || {};
  return String(version.name || version.label || version.id || fallback);
}

function championPosition(state, champion) {
  const explicit = champion?.state?.position_status || champion?.position_status;
  if (explicit) return String(explicit).toUpperCase();
  const openTrade = (state?.trades || []).some((trade) => {
    const metadata = trade?.metadata || {};
    const tradeLane = trade?.lane || metadata.lane;
    return tradeLane === "champion_baseline" && !["won", "lost", "tie", "closed"].includes(String(trade?.result || trade?.status).toLowerCase());
  });
  return openTrade ? "ACTIVE" : "IDLE";
}

export function buildNexusOperationalModel(state = {}, account = null) {
  const champion = lane(state, "champion_baseline");
  const trial = lane(state, "challenger_trial");
  const runtime = state.runtime || {};
  const enabled = Boolean(runtime.champion_enabled ?? runtime.enabled);
  const accountType = String(runtime.champion_account_type || runtime.account_type || account?.account_type || "demo").toLowerCase();
  const positionStatus = championPosition(state, champion);
  const emergency = Boolean(state.emergencyStop ?? runtime.emergency_stop);
  let status = enabled ? `ON — ${accountType === "real" ? "REAL" : "DEMO"}` : "OFF — APRENDENDO EM DEMO";
  if (positionStatus !== "IDLE") status = "AGUARDANDO LIQUIDAÇÃO";
  if (emergency) status = "PARADA TOTAL";
  const pending = (state.proposals || []).filter((proposal) => proposal?.status === "PENDING_USER_REVIEW");
  const completed = Number(state.campaign?.progress?.completed || 0);
  const target = Number(state.campaign?.progress?.target || 300);
  return {
    champion: {
      version: versionLabel(champion, "Champion V1"),
      status,
      statusTone: emergency ? "danger" : positionStatus !== "IDLE" ? "waiting" : enabled ? "live" : "neutral",
      enabled,
      positionStatus,
      stake: enabled ? "GERENCIAMENTO CONFIGURADO" : "US$ 0,35",
      account: enabled ? (runtime.champion_account_id || account?.account_id || "Conta não selecionada") : "DEMO permanente",
      toggleLabel: enabled ? "PARAR CHAMPION" : "INICIAR CHAMPION",
      toggleDisabled: emergency || positionStatus === "RESERVED" || positionStatus === "QUARANTINED",
    },
    trial: {
      version: versionLabel(trial, "Trial V1"),
      status: "LABORATÓRIO DEMO · US$ 0,35",
      hasControls: false,
    },
    campaign: {
      completed,
      target,
      percent: target > 0 ? Math.min(100, Math.max(0, completed / target * 100)) : 0,
      label: `${completed}/${target} operações`,
    },
    emergency,
    proposalPending: pending.length > 0,
    evolutionLabel: pending.length ? `${pending.length} EVOLUÇÃO PENDENTE` : "VER EVOLUÇÃO",
  };
}

function setText(root, selector, value) {
  const node = root?.querySelector?.(selector);
  if (node) node.textContent = value;
}

function renderOperational(root, model) {
  if (!root) return;
  setText(root, "#nexus-champion-status", model.champion.status);
  setText(root, "#nexus-champion-version", model.champion.version);
  setText(root, "#nexus-champion-account", model.champion.account);
  setText(root, "#nexus-champion-stake", model.champion.stake);
  setText(root, "#nexus-trial-version", model.trial.version);
  setText(root, "#nexus-trial-status", model.trial.status);
  setText(root, "#nexus-campaign-progress", model.campaign.label);
  setText(root, "#nexus-open-evolution", model.evolutionLabel);
  const chip = root.querySelector?.("#nexus-champion-status");
  if (chip) chip.className = `status-chip ${model.champion.statusTone}`;
  const progress = root.querySelector?.("#nexus-campaign-progress-bar");
  if (progress) progress.style.width = `${model.campaign.percent}%`;
  const toggle = root.querySelector?.("#nexus-champion-toggle");
  if (toggle) {
    toggle.textContent = model.champion.toggleLabel;
    toggle.disabled = model.champion.toggleDisabled;
    toggle.classList.toggle("stop", model.champion.enabled);
  }
  const stop = root.querySelector?.("#nexus-emergency-stop");
  if (stop) {
    stop.textContent = model.emergency ? "LIBERAR PARADA TOTAL" : "PARADA TOTAL";
    stop.setAttribute("aria-pressed", String(model.emergency));
  }
  const evolution = root.querySelector?.("#nexus-open-evolution");
  if (evolution) evolution.classList.toggle("has-pending", model.proposalPending);
}

const REPORT_METRICS = [
  ["Operações totais", "nTotal"],
  ["Operações decisivas", "nDecisive"],
  ["Wins", "wins"],
  ["Losses", "losses"],
  ["Empates", "ties"],
  ["Assertividade", "accuracy"],
  ["Breakeven", "breakeven"],
  ["Payout médio", "payout"],
  ["Capital arriscado", "capitalAtRisk"],
  ["Payout total", "totalPayout"],
  ["P&L", "profit"],
  ["ROI", "roi"],
  ["Expectancy", "expectancy"],
  ["Profit factor", "profitFactor"],
  ["Drawdown máximo", "drawdown"],
  ["Drawdown normalizado", "drawdownNormalized"],
  ["Recovery factor", "recovery"],
  ["Pior bloco de 50", "worstBlock"],
  ["Pior bloco normalizado", "worstBlockNormalized"],
  ["Maior loss streak", "lossStreak"],
];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function showNode(root, selector, visible) {
  const node = root?.querySelector?.(selector);
  if (node) node.hidden = !visible;
}

function metricRows(view) {
  if (!view) return "";
  return REPORT_METRICS.map(([label, key]) => `<tr><td>${escapeHtml(label)}</td><td>${escapeHtml(view.weekTotal.champion[key])}</td><td>${escapeHtml(view.weekTotal.trial[key])}</td></tr>`).join("");
}

function evidenceLabel(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function renderReports(root, report) {
  const view = buildReportPresentation(report);
  const available = Boolean(view);
  showNode(root, "#nexus-report-empty", !available);
  showNode(root, "#nexus-report-content", available);
  showNode(root, "#nexus-evolution-empty", !available);
  showNode(root, "#nexus-evolution-content", available);
  if (!view) return;

  setText(root, "#nexus-report-week", `ENCERRADA ${view.week || "—"}`);
  const daily = root.querySelector?.("#nexus-report-days");
  if (daily) {
    daily.innerHTML = [
      ...view.days.map((day) => `<tr><td>${escapeHtml(day.date)}</td><td>${escapeHtml(day.champion.nTotal)}</td><td>${escapeHtml(`${day.champion.wins}/${day.champion.losses}/${day.champion.ties}`)}</td><td>${escapeHtml(day.champion.accuracy)}</td><td>${escapeHtml(day.trial.nTotal)}</td><td>${escapeHtml(`${day.trial.wins}/${day.trial.losses}/${day.trial.ties}`)}</td><td>${escapeHtml(day.trial.accuracy)}</td></tr>`),
      `<tr class="nexus-week-total"><td>SEMANA COMPLETA</td><td>${escapeHtml(view.weekTotal.champion.nTotal)}</td><td>${escapeHtml(`${view.weekTotal.champion.wins}/${view.weekTotal.champion.losses}/${view.weekTotal.champion.ties}`)}</td><td>${escapeHtml(view.weekTotal.champion.accuracy)}</td><td>${escapeHtml(view.weekTotal.trial.nTotal)}</td><td>${escapeHtml(`${view.weekTotal.trial.wins}/${view.weekTotal.trial.losses}/${view.weekTotal.trial.ties}`)}</td><td>${escapeHtml(view.weekTotal.trial.accuracy)}</td></tr>`,
    ].join("");
  }
  const rows = metricRows(view);
  const reportMetrics = root.querySelector?.("#nexus-report-metrics");
  const evolutionMetrics = root.querySelector?.("#nexus-evolution-metrics");
  if (reportMetrics) reportMetrics.innerHTML = rows;
  if (evolutionMetrics) evolutionMetrics.innerHTML = rows;

  setText(root, "#nexus-evolution-progress", view.progress.label);
  setText(root, "#nexus-evolution-eligibility", view.progress.eligible
    ? `Elegível para decisão humana · ${view.versions.champion} × ${view.versions.trial}`
    : `Aguardando amostra/tempo · ${view.versions.champion} × ${view.versions.trial}`);
  const progress = root.querySelector?.("#nexus-evolution-progress-bar");
  if (progress) progress.style.width = `${view.progress.percent}%`;
  const recommendation = root.querySelector?.("#nexus-recommendation");
  if (recommendation) {
    recommendation.textContent = view.recommendation;
    recommendation.className = view.recommendation.toLowerCase();
  }
  setText(root, "#nexus-recommendation-reasons", view.recommendationReasons.join(" · ") || "Todos os gates disponíveis foram satisfeitos.");
  const gates = root.querySelector?.("#nexus-evolution-gates");
  if (gates) gates.innerHTML = view.gates.map((gate) => `<tr><td>${escapeHtml(gate.code)}</td><td><span class="nexus-gate-status ${escapeHtml(gate.status.toLowerCase())}">${escapeHtml(gate.status)}</span></td><td>${escapeHtml(`${gate.reason} · observado: ${evidenceLabel(gate.observed)} · limite: ${evidenceLabel(gate.threshold)}`)}</td></tr>`).join("");
}

function reportSnapshot(report) {
  return report?.snapshot && typeof report.snapshot === "object" ? report.snapshot : report;
}

function reportDiffRows(report) {
  const snapshot = reportSnapshot(report);
  if (!snapshot || typeof snapshot !== "object") return [];
  const diffs = snapshot.diffs || {};
  return buildDiff(
    {
      configuration: diffs.configuration?.champion || snapshot.champion?.configuration || {},
      feature_schema: snapshot.champion?.feature_schema || [],
      entry_rules: diffs.entry_rules?.champion || snapshot.champion?.entry_rules || [],
      model: diffs.model?.champion ?? snapshot.champion?.model,
    },
    {
      configuration: diffs.configuration?.trial || snapshot.trial?.configuration || {},
      feature_schema: snapshot.trial?.feature_schema || [],
      entry_rules: diffs.entry_rules?.trial || snapshot.trial?.entry_rules || [],
      model: diffs.model?.trial ?? snapshot.trial?.model,
    },
  );
}

function diffValue(value) {
  if (value === null || value === undefined || value === false) return "—";
  if (value === true) return "presente";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function renderGovernance(root, { store, report, proposal, versions }) {
  const rows = reportDiffRows(report);
  const body = root?.querySelector?.("#nexus-evolution-diff");
  if (body) body.innerHTML = rows.length
    ? rows.map((row) => `<tr><td>${escapeHtml(row.path)}</td><td><span class="nexus-diff-value ${row.before == null ? "empty" : ""}">${escapeHtml(diffValue(row.before))}</span></td><td><span class="nexus-diff-value ${row.after == null ? "empty" : ""}">${escapeHtml(diffValue(row.after))}</span></td><td>${escapeHtml(row.change)}</td></tr>`).join("")
    : `<tr class="empty-row"><td colspan="4">Nenhuma mudança material declarada.</td></tr>`;
  const families = [...new Set(rows.map((row) => row.family))];
  setText(root, "#nexus-diff-family", families.length ? families.join(" · ") : "SEM MUDANÇA");

  const snapshot = store?.get?.() || {};
  const rawReport = reportSnapshot(report);
  const readiness = evaluatePromotionReadiness({ snapshot, proposal, report: rawReport });
  const approve = root?.querySelector?.("#nexus-approve");
  const reanalyze = root?.querySelector?.("#nexus-reanalyze");
  const rollback = root?.querySelector?.("#nexus-rollback");
  if (approve) approve.disabled = !readiness.available;
  if (reanalyze) reanalyze.disabled = !proposal || proposal.status !== "PENDING_USER_REVIEW" || !readiness.expectedRevision;
  const currentChampion = (snapshot.lanes || []).find((lane) => lane?.lane === "champion_baseline")?.version?.id;
  const rollbackTargets = (versions || []).filter((version) => version?.status === "CHAMPION" && version?.id !== currentChampion);
  if (rollback) rollback.disabled = !readiness.expectedRevision || Boolean(snapshot.runtime?.champion_enabled) || rollbackTargets.length === 0;
  setText(root, "#nexus-governance-reason", readiness.available
    ? readiness.requiresReinforced
      ? `Aprovação excepcional exige confirmação reforçada: ${readiness.failedGates.join(", ")}.`
      : "Champion seguro e proposta elegível para decisão humana."
    : readiness.reasons.join(" ") || "Aguardando proposta pendente e Champion seguro.");
  return { readiness, rollbackTargets };
}

export function mountNexusTradeView({
  root = null,
  standardRoot = null,
  store = null,
  api = null,
  getAccount = () => null,
  confirmReal = async () => null,
  onOpenEvolution = () => {},
  onToast = () => {},
} = {}) {
  const locationState = normalizeReportLocation(globalThis.location?.search || "");
  let activeTab = locationState.tab;
  let selectedWeek = locationState.week;
  let selectedReport = null;
  let proposals = [];
  let versions = [];
  let actionMode = null;
  let catalogRefreshQueued = false;

  const renderTabs = () => {
    for (const button of root?.querySelectorAll?.("[data-nexus-tab]") || []) {
      const selected = button.dataset.nexusTab === activeTab;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
    }
    showNode(root, "#nexus-operational-panel", activeTab === "operations");
    showNode(root, "#nexus-reports-panel", activeTab === "reports");
    showNode(root, "#nexus-evolution-panel", activeTab === "evolution");
  };
  const render = (notifiedState = null) => {
    const state = notifiedState || store?.get?.() || {};
    const reportId = reportSnapshot(selectedReport)?.id || selectedReport?.id;
    const eventReport = reportId ? (state.reports || []).find((item) => (reportSnapshot(item)?.id || item?.id) === reportId) : null;
    if (eventReport) selectedReport = eventReport;
    if (Array.isArray(state.proposals) && state.proposals.length) {
      const eventIds = new Set(state.proposals.map((item) => item?.id).filter(Boolean));
      proposals = [...state.proposals, ...proposals.filter((item) => !eventIds.has(item?.id))];
    }
    renderOperational(root, buildNexusOperationalModel(state, getAccount()));
    renderTabs();
    renderReports(root, selectedReport);
    const report = reportSnapshot(selectedReport);
    const proposal = proposals.find((item) => item?.status === "PENDING_USER_REVIEW" && (!report?.campaign_id || item?.campaign_id === report.campaign_id)) || null;
    renderGovernance(root, { store, report: selectedReport, proposal, versions });
    for (const button of root?.querySelectorAll?.('[data-nexus-action^="export-"]') || []) {
      button.disabled = !reportId;
    }
  };

  const writeLocation = () => {
    if (!globalThis.history?.replaceState || !globalThis.location) return;
    const search = reportLocationSearch(globalThis.location.search, { tab: activeTab, week: selectedWeek });
    globalThis.history.replaceState(null, "", `${globalThis.location.pathname}${search}${globalThis.location.hash || ""}`);
  };

  const loadReportWeek = async (week) => {
    if (!api || !week) return;
    try {
      selectedReport = await api.weeklyReport(week);
    } catch (error) {
      selectedReport = null;
      if (error?.status !== 404) onToast(error.message || "Falha ao carregar a semana.", "error");
    }
    render();
  };

  const loadReports = async () => {
    if (!api) return;
    try {
      const [reports, fetchedProposals, fetchedVersions] = await Promise.all([
        api.reports(), api.proposals(), api.versions(),
      ]);
      proposals = Array.isArray(fetchedProposals) ? fetchedProposals : [];
      versions = Array.isArray(fetchedVersions) ? fetchedVersions : [];
      const weekly = (Array.isArray(reports) ? reports : [])
        .map((item) => ({ item, view: buildReportPresentation(item) }))
        .filter(({ view }) => view?.type === "weekly");
      if (selectedWeek) {
        selectedReport = weekly.find(({ view }) => view.week === selectedWeek)?.item || null;
        if (!selectedReport) await loadReportWeek(selectedWeek);
      } else {
        selectedReport = weekly[0]?.item || null;
        selectedWeek = weekly[0]?.view?.week || null;
      }
      writeLocation();
      render();
    } catch (error) {
      selectedReport = null;
      render();
      onToast(error.message || "Falha ao carregar relatórios.", "error");
    }
  };

  const scheduleCatalogRefresh = () => {
    if (catalogRefreshQueued || !api) return;
    catalogRefreshQueued = true;
    const enqueue = globalThis.queueMicrotask || ((callback) => Promise.resolve().then(callback));
    enqueue(async () => {
      try { await loadReports(); } finally { catalogRefreshQueued = false; }
    });
  };

  store?.subscribe?.((state, change) => {
    render(state);
    if (change?.kind === "event" && [
      "nexus.report", "nexus.proposal", "nexus.campaign", "nexus.trial_changed", "nexus.version_changed",
    ].includes(change.type)) scheduleCatalogRefresh();
  });

  const openTab = (tab) => {
    activeTab = ["operations", "reports", "evolution"].includes(tab) ? tab : "operations";
    writeLocation();
    render();
    if (activeTab !== "operations") loadReports();
  };

  const handleToggle = async () => {
    if (!api || !store) return;
    const state = store.get();
    const model = buildNexusOperationalModel(state, getAccount());
    const account = getAccount();
    try {
      if (model.champion.enabled) {
        const snapshot = await api.setMode({
          enabled: false,
          account_id: state.runtime?.champion_account_id || "",
          account_type: "demo",
          real_ticket: "",
        });
        store.hydrate(snapshot);
        onToast("Champion parado. O laboratório DEMO continua ativo.");
        return;
      }
      if (!account) throw new Error("Selecione uma conta global antes de iniciar o Champion.");
      let realTicket = "";
      if (account.account_type === "real") {
        realTicket = await confirmReal(account);
        if (!realTicket) return;
      }
      const snapshot = await api.setMode({
        enabled: true,
        account_id: account.account_id,
        account_type: account.account_type,
        real_ticket: realTicket,
      });
      store.hydrate(snapshot);
      onToast(`Champion ON — ${account.account_type === "real" ? "REAL" : "DEMO"}.`);
    } catch (error) {
      onToast(error.message || "Falha ao alterar o Champion.", "error");
    }
  };

  const handleEmergency = async () => {
    if (!api || !store) return;
    try {
      const snapshot = await api.emergencyStop(!buildNexusOperationalModel(store.get()).emergency);
      store.hydrate(snapshot);
      onToast(snapshot.emergency_stop ? "Parada total ativada." : "Parada total liberada.");
    } catch (error) {
      onToast(error.message || "Falha na parada total.", "error");
    }
  };

  const governanceContext = () => {
    const report = reportSnapshot(selectedReport);
    const proposal = proposals.find((item) => item?.status === "PENDING_USER_REVIEW" && (!report?.campaign_id || item?.campaign_id === report.campaign_id)) || null;
    const rendered = renderGovernance(root, { store, report: selectedReport, proposal, versions });
    return { report, proposal, ...rendered };
  };

  const closeGovernance = () => {
    const dialog = globalThis.document?.querySelector?.("#nexus-governance-dialog");
    if (dialog) dialog.hidden = true;
    const humanKey = globalThis.document?.querySelector?.("#nexus-human-key");
    const reason = globalThis.document?.querySelector?.("#nexus-governance-justification");
    const reinforced = globalThis.document?.querySelector?.("#nexus-reinforced-confirmation");
    const error = globalThis.document?.querySelector?.("#nexus-governance-error");
    if (humanKey) humanKey.value = "";
    if (reason) reason.value = "";
    if (reinforced) reinforced.checked = false;
    if (error) error.hidden = true;
    actionMode = null;
  };

  const openGovernance = (mode) => {
    const { proposal, readiness, rollbackTargets } = governanceContext();
    if (mode === "approve" && !readiness.available) {
      onToast(readiness.reasons.join(" ") || "A aprovação ainda está bloqueada.", "error");
      return;
    }
    if (["approve", "reanalyze"].includes(mode) && !proposal) {
      onToast("Não existe proposta pendente para esta campanha.", "error");
      return;
    }
    if (mode === "rollback" && rollbackTargets.length === 0) {
      onToast("Não existe versão Champion histórica elegível para rollback.", "error");
      return;
    }
    actionMode = mode;
    const dialog = globalThis.document?.querySelector?.("#nexus-governance-dialog");
    const title = globalThis.document?.querySelector?.("#nexus-governance-title");
    const summary = globalThis.document?.querySelector?.("#nexus-governance-summary");
    const reinforcedField = globalThis.document?.querySelector?.("#nexus-reinforced-field");
    const rollbackField = globalThis.document?.querySelector?.("#nexus-rollback-target-field");
    const rollbackSelect = globalThis.document?.querySelector?.("#nexus-rollback-target");
    if (title) title.textContent = ({ approve: "Aprovar evolução do Champion", reanalyze: "Enviar Trial à reanálise", rollback: "Reverter Champion" })[mode];
    if (summary) summary.textContent = mode === "approve"
      ? `A versão só mudará após commit do backend. Gates não aprovados: ${readiness.failedGates.join(", ") || "nenhum"}.`
      : mode === "reanalyze" ? "A campanha visual será reiniciada, mas o aprendizado interno continuará governado."
        : "O rollback cria uma transição auditada; nenhuma versão histórica será apagada.";
    if (reinforcedField) reinforcedField.hidden = !(mode === "approve" && readiness.requiresReinforced);
    if (rollbackField) rollbackField.hidden = mode !== "rollback";
    if (rollbackSelect) rollbackSelect.innerHTML = rollbackTargets.map((version) => `<option value="${escapeHtml(version.id)}" data-version-hash="${escapeHtml(version.version_hash)}">${escapeHtml(version.id)}</option>`).join("");
    if (dialog) dialog.hidden = false;
    globalThis.document?.querySelector?.("#nexus-governance-justification")?.focus?.();
  };

  const submitGovernance = async (event) => {
    event?.preventDefault?.();
    if (!actionMode || !api || !store) return;
    const humanKeyNode = globalThis.document?.querySelector?.("#nexus-human-key");
    const reasonNode = globalThis.document?.querySelector?.("#nexus-governance-justification");
    const reinforcedNode = globalThis.document?.querySelector?.("#nexus-reinforced-confirmation");
    const submit = globalThis.document?.querySelector?.("#nexus-submit-governance");
    const errorNode = globalThis.document?.querySelector?.("#nexus-governance-error");
    const { proposal, readiness } = governanceContext();
    const humanKey = String(humanKeyNode?.value || "");
    if (humanKeyNode) humanKeyNode.value = "";
    try {
      if (actionMode === "approve" && readiness.requiresReinforced && reinforcedNode?.checked !== true) {
        throw new Error("Marque a confirmação reforçada para aceitar os gates negociáveis falhos.");
      }
      const payload = buildGovernancePayload({
        expectedRevision: readiness.expectedRevision,
        reason: reasonNode?.value,
        reinforcedConfirmation: actionMode === "approve" && reinforcedNode?.checked === true,
      });
      if (!humanKey.trim()) throw new Error("A credencial humana temporária é obrigatória.");
      if (submit) submit.disabled = true;
      let result;
      if (actionMode === "approve") result = await api.approve(proposal.id, payload, humanKey);
      else if (actionMode === "reanalyze") result = await api.reanalyze(proposal.id, payload, humanKey);
      else {
        const target = globalThis.document?.querySelector?.("#nexus-rollback-target")?.selectedOptions?.[0];
        result = await api.rollback({
          ...payload,
          target_version_id: target?.value || "",
          target_version_hash: target?.dataset?.versionHash || "",
        }, humanKey);
      }
      if (result?.snapshot) store.hydrate(result.snapshot);
      const transition = result?.transition || {};
      setText(root, "#nexus-governance-result", `Ação confirmada · ${transition.audit_id || transition.request_id || transition.outcome || "auditada"}`);
      closeGovernance();
      await loadReports();
      onToast("Decisão NexusTrade confirmada pelo backend.");
    } catch (error) {
      if (error?.status === 409) {
        try { store.hydrate(await api.snapshot()); } catch { /* original conflict remains visible */ }
        await loadReports();
      }
      if (errorNode) { errorNode.textContent = error.message || "Falha na ação governada."; errorNode.hidden = false; }
      onToast(error.message || "Falha na ação governada.", "error");
    } finally {
      if (humanKeyNode) humanKeyNode.value = "";
      if (submit) submit.disabled = false;
    }
  };

  const handleExport = async (format) => {
    const report = reportSnapshot(selectedReport);
    const reportId = report?.id || selectedReport?.id;
    if (!api || !reportId) {
      onToast("Selecione uma semana com relatório antes de exportar.", "error");
      return;
    }
    const buttons = [...(root?.querySelectorAll?.('[data-nexus-action^="export-"]') || [])];
    buttons.forEach((button) => { button.disabled = true; });
    setText(root, "#nexus-export-status", "Gerando arquivo imutável…");
    try {
      const download = await api.downloadReport(reportId, format);
      saveNexusDownload(download);
      setText(root, "#nexus-export-status", download.filename);
      onToast(`Exportação pronta: ${download.filename}`);
    } catch (error) {
      setText(root, "#nexus-export-status", "Falha na exportação");
      onToast(error.message || "Falha na exportação.", "error");
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  };

  globalThis.document?.querySelector?.("#nexus-governance-form")?.addEventListener?.("submit", submitGovernance);
  globalThis.document?.querySelector?.("#nexus-cancel-governance")?.addEventListener?.("click", closeGovernance);

  root?.addEventListener?.("click", (event) => {
    const tab = event.target?.closest?.("[data-nexus-tab]")?.dataset?.nexusTab;
    if (tab) openTab(tab);
    const action = event.target?.closest?.("[data-nexus-action]")?.dataset?.nexusAction;
    if (action === "champion-toggle") handleToggle();
    if (action === "emergency-stop") handleEmergency();
    if (action === "open-evolution") { openTab("evolution"); onOpenEvolution(); }
    if (["approve", "reanalyze", "rollback"].includes(action)) openGovernance(action);
    if (action === "export-zip") handleExport("csv.zip");
    if (action === "export-xlsx") handleExport("xlsx");
    if (["previous-week", "next-week"].includes(action)) {
      const target = shiftAlignedWeek(selectedWeek, action === "previous-week" ? -1 : 1);
      if (target) {
        selectedWeek = target;
        writeLocation();
        loadReportWeek(target);
      }
    }
  });
  render();

  return {
    show() {
      if (root) root.hidden = false;
      if (standardRoot) standardRoot.hidden = true;
      render();
      if (activeTab !== "operations") loadReports();
    },
    hide() {
      if (root) root.hidden = true;
      if (standardRoot) standardRoot.hidden = false;
    },
    render,
    openTab,
    refreshReports: loadReports,
  };
}

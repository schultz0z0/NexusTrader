import { NEXUS_BOT_ID } from "./nexus_trade_store.js";

export function resolveDashboardView(botId) {
  return botId === NEXUS_BOT_ID ? "nexus" : "standard";
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
  const render = () => renderOperational(root, buildNexusOperationalModel(store?.get?.() || {}, getAccount()));
  store?.subscribe?.(render);

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

  root?.addEventListener?.("click", (event) => {
    const action = event.target?.closest?.("[data-nexus-action]")?.dataset?.nexusAction;
    if (action === "champion-toggle") handleToggle();
    if (action === "emergency-stop") handleEmergency();
    if (action === "open-evolution") onOpenEvolution();
  });
  render();

  return {
    show() {
      if (root) root.hidden = false;
      if (standardRoot) standardRoot.hidden = true;
      render();
    },
    hide() {
      if (root) root.hidden = true;
      if (standardRoot) standardRoot.hidden = false;
    },
    render,
  };
}

import assert from "node:assert/strict";
import test from "node:test";

import {
  buildNexusOperationalModel,
  championManagementPayload,
  mountNexusTradeView,
  resolveDashboardView,
} from "../../static/js/nexus_trade_view.js";

test("only the fixed NexusTrade bot routes to the dedicated view", () => {
  assert.equal(resolveDashboardView("nexus-trade"), "nexus");
  assert.equal(resolveDashboardView("donchian-a"), "standard");
  assert.equal(resolveDashboardView("nexus-speed"), "standard");
});

test("the view controller toggles its owned root without touching standard content", () => {
  const root = { hidden: true };
  const standardRoot = { hidden: false };
  const controller = mountNexusTradeView({ root, standardRoot });

  controller.show();
  assert.equal(root.hidden, false);
  assert.equal(standardRoot.hidden, true);

  controller.hide();
  assert.equal(root.hidden, true);
  assert.equal(standardRoot.hidden, false);
});

test("management dialog rehydrates a stale active snapshot before blocking Champion start", async () => {
  const listeners = new Map();
  const root = {
    hidden: true,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (type, handler) => listeners.set(type, handler),
  };
  const standardRoot = { hidden: false };
  const dialog = { hidden: true };
  const submit = { textContent: "" };
  const managementField = { value: "", focus() {} };
  const moneyManagementField = { value: "fixed" };
  const form = {
    elements: {
      initial_stake: managementField,
      money_management: moneyManagementField,
      expected_revision: { value: "" },
      multiplier: { value: "" },
      max_levels: { value: "" },
      levels: { value: "" },
      percent: { value: "" },
    },
  };
  const documentStub = {
    activeElement: null,
    querySelector(selector) {
      if (selector === "#nexus-management-form") return form;
      if (selector === "#nexus-management-dialog") return dialog;
      if (selector === "#nexus-save-management") return submit;
      return null;
    },
  };
  const staleState = operationalState({ positionStatus: "ACTIVE" });
  const freshState = operationalState({ positionStatus: "IDLE" });
  let state = structuredClone(staleState);
  const store = {
    get: () => structuredClone(state),
    hydrate(snapshot) {
      state = structuredClone({
        ...state,
        runtime: snapshot.runtime,
        emergencyStop: snapshot.emergency_stop ?? state.emergencyStop,
        championManagement: snapshot.champion_management,
        lanes: snapshot.lanes,
        laneStates: snapshot.lane_states || state.laneStates,
      });
      return true;
    },
    subscribe: () => () => {},
  };
  const api = {
    snapshot: async () => ({
      schema_version: 1,
      snapshot_version: 2,
      runtime: freshState.runtime,
      emergency_stop: false,
      champion_management: {
        revision: 2,
        initial_stake: 0.35,
        money_management: "fixed",
        money_config: {},
        risk_config: {},
      },
      lanes: freshState.lanes,
      lane_states: {
        champion_baseline: { position_status: "IDLE" },
        challenger_trial: { position_status: "IDLE" },
      },
    }),
  };

  const previousDocument = globalThis.document;
  globalThis.document = documentStub;
  try {
    mountNexusTradeView({ root, standardRoot, store, api });
    listeners.get("click")({
      target: { closest: (selector) => (selector === "[data-nexus-action]" ? { dataset: { nexusAction: "open-management" } } : null) },
    });
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(dialog.hidden, false);
    assert.equal(submit.textContent, "SALVAR GERENCIAMENTO");
  } finally {
    globalThis.document = previousDocument;
  }
});

function operationalState(overrides = {}) {
  return {
    runtime: { enabled: 0, account_type: "demo", ...overrides.runtime },
    emergencyStop: Boolean(overrides.emergencyStop),
    championSession: overrides.championSession || {
      management_active: false,
      mode: "off",
      baseline_account_type: "demo",
      baseline_initial_stake: 0.35,
      suggestion: {
        revision: 7,
        initial_stake: 1.5,
        money_management: "soros",
        money_config: { levels: 2, percent: 0.6 },
        risk_config: { take_profit_daily: 25, stop_loss_daily: 12 },
      },
      active_management: null,
    },
    championLastHour: overrides.championLastHour || {
      window_seconds: 3600,
      closed_trades: 3,
      wins: 1,
      losses: 1,
      ties: 1,
      decisive_trades: 2,
      accuracy: 0.5,
    },
    lanes: [
      {
        lane: "champion_baseline",
        state: { position_status: overrides.positionStatus || "IDLE" },
        version: { id: "champion-v3", status: "CHAMPION" },
      },
      {
        lane: "challenger_trial",
        state: { position_status: "IDLE" },
        version: { id: "trial-b", status: "TRIAL" },
      },
    ],
    campaign: { progress: { completed: 127, target: 300 } },
    proposals: overrides.proposals || [],
  };
}

test("Champion OFF and Trial are represented as separate protected lanes", () => {
  const model = buildNexusOperationalModel(operationalState());

  assert.equal(model.champion.status, "OFF — APRENDENDO EM DEMO");
  assert.equal(model.champion.stake, "US$ 0,35");
  assert.equal(model.champion.toggleLabel, "INICIAR CHAMPION");
  assert.equal(model.trial.status, "LABORATÓRIO DEMO · US$ 0,35");
  assert.equal(model.trial.hasControls, false);
  assert.equal(model.campaign.label, "127/300 operações");
});

test("ON DEMO, ON REAL, settlement, emergency and proposal states are explicit", () => {
  assert.equal(
    buildNexusOperationalModel(operationalState({ runtime: { enabled: 1, account_type: "demo" } })).champion.status,
    "ON — DEMO",
  );
  assert.equal(
    buildNexusOperationalModel(operationalState({ runtime: { enabled: 1, account_type: "real" } })).champion.status,
    "ON — REAL",
  );
  assert.equal(
    buildNexusOperationalModel(operationalState({ positionStatus: "ACTIVE" })).champion.status,
    "AGUARDANDO LIQUIDAÇÃO",
  );
  assert.equal(
    buildNexusOperationalModel(operationalState({ emergencyStop: true })).champion.status,
    "PARADA TOTAL",
  );
  const proposal = buildNexusOperationalModel(operationalState({
    proposals: [{ id: "proposal-1", status: "PENDING_USER_REVIEW" }],
  }));
  assert.equal(proposal.proposalPending, true);
  assert.equal(proposal.evolutionLabel, "1 EVOLUÇÃO PENDENTE");
});

test("Champion management is editable only while OFF and IDLE", () => {
  const state = operationalState();
  state.championManagement = {
    revision: 7,
    initial_stake: 1.5,
    money_management: "soros",
    money_config: { levels: 2, percent: 0.6 },
    risk_config: { take_profit_daily: 25, stop_loss_daily: 12, max_single_stake: 4 },
  };
  const model = buildNexusOperationalModel(state);

  assert.equal(model.champion.management.revision, 7);
  assert.equal(model.champion.management.initial_stake, 1.5);
  assert.equal(model.champion.managementEditable, true);
  assert.equal(buildNexusOperationalModel({ ...state, runtime: { enabled: 1 } }).champion.managementEditable, false);
  assert.equal(buildNexusOperationalModel(operationalState({ positionStatus: "ACTIVE" })).champion.managementEditable, false);
});

test("Champion OFF shows the next-session suggestion and last-hour summary", () => {
  const model = buildNexusOperationalModel(operationalState());

  assert.equal(model.champion.sessionLabel, "SUGESTAO PARA A PROXIMA SESSAO");
  assert.equal(model.champion.sessionSummary, "US$ 1.50 · SOROS");
  assert.equal(model.champion.sessionHint, "OFF segue em DEMO US$ 0,35 sem gerenciamento ativo.");
  assert.equal(model.champion.lastHourLabel, "ULTIMA HORA DO CHAMPION");
  assert.equal(model.champion.lastHourSummary, "1/2 (50,00%)");
});

test("Champion ON promotes the same config to the active session summary", () => {
  const model = buildNexusOperationalModel(operationalState({
    runtime: { enabled: 1, account_type: "demo" },
    championSession: {
      management_active: true,
      mode: "on",
      baseline_account_type: "demo",
      baseline_initial_stake: 0.35,
      suggestion: {
        revision: 7,
        initial_stake: 1.5,
        money_management: "soros",
        money_config: { levels: 2, percent: 0.6 },
        risk_config: { take_profit_daily: 25, stop_loss_daily: 12 },
      },
      active_management: {
        revision: 7,
        initial_stake: 1.5,
        money_management: "soros",
        money_config: { levels: 2, percent: 0.6 },
        risk_config: { take_profit_daily: 25, stop_loss_daily: 12 },
      },
    },
  }));

  assert.equal(model.champion.sessionLabel, "GERENCIAMENTO DA SESSAO");
  assert.equal(model.champion.sessionSummary, "US$ 1.50 · SOROS");
  assert.equal(model.champion.sessionHint, "A sessao ON usa esta configuracao ate voce desligar o Champion.");
});

test("management form payload preserves only the approved backend contract", () => {
  assert.deepEqual(championManagementPayload({
    initial_stake: "1.25", money_management: "martingale",
    multiplier: "2", max_levels: "3", take_profit_daily: "20",
    stop_loss_daily: "10", max_daily_trades: "50", max_single_stake: "5",
    max_consecutive_losses: "3", cooldown_minutes: "15",
  }, 4), {
    expected_revision: 4,
    initial_stake: 1.25,
    money_management: "martingale",
    money_config: { multiplier: 2, max_levels: 3 },
    risk_config: {
      take_profit_daily: 20, stop_loss_daily: 10, max_daily_trades: 50,
      max_single_stake: 5, max_consecutive_losses: 3, cooldown_minutes: 15,
    },
  });
  assert.deepEqual(championManagementPayload({
    initial_stake: "2", money_management: "soros", levels: "2", percent: "60",
  }, 5).money_config, { levels: 2, percent: 0.6 });
});

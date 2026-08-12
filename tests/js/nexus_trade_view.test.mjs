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

function operationalState(overrides = {}) {
  return {
    runtime: { enabled: 0, account_type: "demo", ...overrides.runtime },
    emergencyStop: Boolean(overrides.emergencyStop),
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

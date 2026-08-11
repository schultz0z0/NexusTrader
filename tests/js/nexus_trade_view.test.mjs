import assert from "node:assert/strict";
import test from "node:test";

import {
  buildNexusOperationalModel,
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

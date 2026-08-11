import test from "node:test";
import assert from "node:assert/strict";

import {
  buildDiff,
  buildGovernancePayload,
  evaluatePromotionReadiness,
} from "../../static/js/nexus_trade_diff.js";

const passGate = (code) => ({ code, status: "PASS", observed: true, threshold: true, reason: "verificado" });
const HARD = ["MINIMUM_SAMPLE", "DATA_INTEGRITY", "COMPARABLE_PROVENANCE", "RISK_LIMITS", "CHANGE_BUDGET"];

function safeContext(overrides = {}) {
  return {
    snapshot: {
      snapshotVersion: 17,
      runtime: { champion_enabled: 0 },
      lanes: [{ lane: "champion_baseline", state: { position_status: "IDLE" } }],
      trades: [],
      ...overrides.snapshot,
    },
    proposal: { id: "proposal-a", status: "PENDING_USER_REVIEW", ...overrides.proposal },
    report: {
      recommendation: "EVOLVE",
      gates: [...HARD.map(passGate), passGate("PROFIT_FACTOR")],
      ...overrides.report,
    },
  };
}

test("diff explains nested configuration, indicators, features, rules and model", () => {
  const rows = buildDiff(
    {
      configuration: { bollinger: { period: 20, deviation: 2 }, adx: { threshold: 22 }, rsi: { period: 14 } },
      feature_schema: ["percent_b", "adx"],
      entry_rules: ["bollinger", "adx_gate"],
      model: "deterministic-v1",
    },
    {
      configuration: { bollinger: { period: 18, deviation: 2 }, adx: { threshold: 21 }, chop: { period: 14 } },
      feature_schema: ["percent_b", "adx", "chop"],
      entry_rules: ["bollinger", "adx_gate", "ml_gate"],
      model: "hgb-v1",
    },
  );

  assert.deepEqual(rows.find((row) => row.path === "bollinger.period"), {
    path: "bollinger.period", before: 20, after: 18, change: "modified", family: "indicator_configuration",
  });
  assert.equal(rows.find((row) => row.path === "rsi").change, "removed");
  assert.equal(rows.find((row) => row.path === "chop").change, "added");
  assert.equal(rows.find((row) => row.path === "features.chop").change, "added");
  assert.equal(rows.find((row) => row.path === "entry_rules.ml_gate").change, "added");
  assert.equal(rows.find((row) => row.path === "model").change, "modified");
});

test("approval is available only while Champion is OFF, safe and hard gates pass", () => {
  assert.deepEqual(evaluatePromotionReadiness(safeContext()), {
    available: true,
    requiresReinforced: false,
    expectedRevision: 17,
    reasons: [],
    failedGates: [],
  });

  for (const snapshot of [
    { runtime: { champion_enabled: 1 } },
    { lanes: [{ lane: "champion_baseline", state: { position_status: "ACTIVE" } }] },
    { trades: [{ lane: "champion_baseline", status: "open" }] },
  ]) {
    assert.equal(evaluatePromotionReadiness(safeContext({ snapshot })).available, false);
  }
  const hardFailure = safeContext({ report: { gates: [
    ...HARD.filter((code) => code !== "DATA_INTEGRITY").map(passGate),
    { ...passGate("DATA_INTEGRITY"), status: "FAIL" },
  ] } });
  assert.equal(evaluatePromotionReadiness(hardFailure).available, false);
  assert.deepEqual(evaluatePromotionReadiness(hardFailure).failedGates, ["DATA_INTEGRITY"]);
});

test("reanalyze recommendation requires an explicit reinforced confirmation", () => {
  const context = safeContext({ report: {
    recommendation: "REANALYZE",
    gates: [...HARD.map(passGate), { ...passGate("PROFIT_FACTOR"), status: "FAIL" }],
  } });
  const readiness = evaluatePromotionReadiness(context);

  assert.equal(readiness.available, true);
  assert.equal(readiness.requiresReinforced, true);
  assert.deepEqual(readiness.failedGates, ["PROFIT_FACTOR"]);

  const payload = buildGovernancePayload({
    expectedRevision: readiness.expectedRevision,
    reason: "Revisei a semana completa e aceito o risco documentado.",
    reinforcedConfirmation: true,
    requestId: "human-action-1",
    humanKey: "must-never-enter-body",
  });
  assert.deepEqual(payload, {
    expected_revision: 17,
    request_id: "human-action-1",
    reason: "Revisei a semana completa e aceito o risco documentado.",
    reinforced_confirmation: true,
  });
  assert.equal(JSON.stringify(payload).includes("must-never-enter-body"), false);
});

test("inconclusive, stale or non-pending proposals stay unavailable", () => {
  assert.equal(evaluatePromotionReadiness(safeContext({ report: { recommendation: "INCONCLUSIVE" } })).available, false);
  assert.equal(evaluatePromotionReadiness(safeContext({ proposal: { status: "APPROVED" } })).available, false);
  assert.equal(evaluatePromotionReadiness(safeContext({ snapshot: { snapshotVersion: 0 } })).available, false);
});

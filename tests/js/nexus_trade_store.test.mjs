import assert from "node:assert/strict";
import test from "node:test";

import { createNexusTradeStore } from "../../static/js/nexus_trade_store.js";

const baseSnapshot = (revision = 4) => ({
  schema_version: 1,
  snapshot_version: revision,
  runtime: { id: "nexus-trade", enabled: 0, account_type: "demo" },
  emergency_stop: false,
  lanes: [
    { lane: "champion_baseline", version: { id: "champion-v1", status: "CHAMPION" } },
    { lane: "challenger_trial", version: { id: "trial-v1", status: "TRIAL" } },
  ],
  active_campaigns: [{ id: "campaign-a", completed: 12, target: 300 }],
  decisions: [],
  trades: [],
  reports: [],
  proposals: [],
});

const event = (type, eventId, revision, payload) => ({
  type,
  event_id: eventId,
  schema_version: 1,
  snapshot_version: revision,
  bot_id: "nexus-trade",
  payload,
});

test("hydrate normalizes the durable snapshot without exposing caller mutation", () => {
  const source = baseSnapshot();
  const store = createNexusTradeStore();

  assert.equal(store.hydrate(source), true);
  source.runtime.enabled = 1;
  source.active_campaigns[0].completed = 299;

  assert.equal(store.get().runtime.enabled, 0);
  assert.equal(store.get().campaign.progress.completed, 12);
  assert.equal(store.get().campaign.progress.target, 300);
  assert.equal(store.get().snapshotVersion, 4);
});

test("duplicate and older Nexus events are ignored while one revision may contain distinct events", () => {
  const store = createNexusTradeStore(baseSnapshot());
  const changed = event("nexus.trial_changed", "evt-1", 5, {
    id: "trial-change-a",
    version: { id: "trial-v2", status: "TRIAL" },
    campaign: { id: "campaign-b", completed: 0, target: 300 },
  });

  assert.equal(store.apply(changed), true);
  assert.equal(store.apply(changed), false);
  assert.equal(store.apply(event("nexus.report", "evt-old", 3, { id: "old-report" })), false);
  assert.equal(store.apply(event("nexus.report", "evt-2", 5, { id: "report-5" })), true);

  const state = store.get();
  assert.equal(state.auditEvents.length, 2);
  assert.equal(state.campaign.progress.completed, 0);
  assert.equal(state.trialChange.version.id, "trial-v2");
  assert.equal(state.reports[0].id, "report-5");
});

test("a reconnect snapshot wins only when its revision is current or newer", () => {
  const store = createNexusTradeStore(baseSnapshot(8));
  const observed = [];
  const unsubscribe = store.subscribe((state) => observed.push(state.snapshotVersion));

  assert.equal(store.hydrate(baseSnapshot(7)), false);
  const repaired = baseSnapshot(9);
  repaired.reports = [{ id: "weekly-9" }];
  assert.equal(store.hydrate(repaired), true);
  assert.equal(store.get().reports[0].id, "weekly-9");
  unsubscribe();
  store.hydrate(baseSnapshot(10));

  assert.deepEqual(observed, [9]);
});

test("invalid envelopes and client-visible secrets fail closed", () => {
  const store = createNexusTradeStore(baseSnapshot());

  assert.equal(store.apply({ type: "nexus.report", payload: { id: "missing-envelope" } }), false);
  assert.equal(store.apply(event("nexus.report", "evt-secret", 5, {
    id: "secret-report",
    api_token: "must-not-enter-client-state",
  })), false);
  assert.equal(store.get().auditEvents.length, 0);
});

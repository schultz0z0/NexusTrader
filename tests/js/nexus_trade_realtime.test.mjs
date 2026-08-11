import assert from "node:assert/strict";
import test from "node:test";

import {
  createNexusTradeStore,
  reconcileNexusTradeStore,
} from "../../static/js/nexus_trade_store.js";
import {
  handleGovernanceDialogKeydown,
  saveNexusDownload,
} from "../../static/js/nexus_trade_view.js";
import { evaluatePromotionReadiness } from "../../static/js/nexus_trade_diff.js";

const snapshot = (revision, overrides = {}) => ({
  schema_version: 1,
  snapshot_version: revision,
  runtime: { enabled: 0, champion_version_id: "champion-v1" },
  emergency_stop: false,
  lanes: [
    { lane: "champion_baseline", state: { position_status: "IDLE" }, version: { id: "champion-v1", status: "CHAMPION" } },
    { lane: "challenger_trial", state: { position_status: "IDLE" }, version: { id: "trial-v1", status: "TRIAL" } },
  ],
  active_campaigns: [],
  decisions: [],
  trades: [],
  reports: [],
  proposals: [],
  ...overrides,
});

const event = (type, id, revision, payload) => ({
  type,
  event_id: id,
  schema_version: 1,
  snapshot_version: revision,
  bot_id: "nexus-trade",
  payload,
});

test("reconnect stays unavailable until one current REST snapshot is committed", async () => {
  const store = createNexusTradeStore(snapshot(7));
  store.setConnection("stale");
  let release;
  const pending = new Promise((resolve) => { release = resolve; });

  const repair = reconcileNexusTradeStore(store, async () => {
    await pending;
    return snapshot(8, { reports: [{ id: "weekly-8" }] });
  });

  assert.equal(store.get().connection.status, "connecting");
  assert.equal(evaluatePromotionReadiness({ snapshot: store.get(), proposal: null, report: null }).available, false);
  release();
  assert.equal(await repair, true);
  assert.equal(store.get().connection.status, "live");
  assert.equal(store.get().reports[0].id, "weekly-8");
});

test("failed or stale reconnect fails closed and never rolls state backwards", async () => {
  const store = createNexusTradeStore(snapshot(9));

  await assert.rejects(reconcileNexusTradeStore(store, async () => snapshot(8)), /stale|inválido/i);
  assert.equal(store.get().snapshotVersion, 9);
  assert.equal(store.get().connection.status, "stale");
  await assert.rejects(reconcileNexusTradeStore(store, async () => { throw new Error("offline"); }), /offline/);
  assert.equal(store.get().connection.status, "stale");
});

test("report, proposal, campaign and version events notify one committed cross-view state", () => {
  const store = createNexusTradeStore(snapshot(4));
  const changes = [];
  store.subscribe((state, change) => changes.push({ state, change }));

  store.apply(event("nexus.report", "report-e", 5, { id: "weekly-5", report_type: "weekly" }));
  store.apply(event("nexus.proposal", "proposal-e", 5, { id: "proposal-5", status: "PENDING_USER_REVIEW" }));
  store.apply(event("nexus.campaign", "campaign-e", 5, { id: "campaign-5", status: "ACTIVE", completed: 0, target: 300 }));
  store.apply(event("nexus.version_changed", "version-e", 5, { lane: "champion_baseline", version: { id: "champion-v2", status: "CHAMPION" } }));

  assert.deepEqual(changes.map((item) => item.change.type), [
    "nexus.report", "nexus.proposal", "nexus.campaign", "nexus.version_changed",
  ]);
  assert.equal(changes[0].state.reports[0].id, "weekly-5");
  assert.equal(changes[1].state.proposals[0].id, "proposal-5");
  assert.equal(changes[2].state.campaign.current.id, "campaign-5");
  assert.equal(changes[3].state.lanes.find((lane) => lane.lane === "champion_baseline").version.id, "champion-v2");
});

test("download is saved once, keeps the server filename and always revokes its URL", () => {
  const clicks = [];
  const revoked = [];
  const anchor = { click: () => clicks.push("clicked"), remove() {}, download: "", href: "" };
  const documentRef = {
    body: { append() {} },
    createElement: (tag) => tag === "a" ? anchor : null,
  };
  const urlRef = {
    createObjectURL: () => "blob:nexus-export",
    revokeObjectURL: (url) => revoked.push(url),
  };

  const saved = saveNexusDownload({
    filename: "nexustrade-2026-08-17-campaign-a-abcdef12.xlsx",
    blob: new Blob([new Uint8Array([1, 2, 3])]),
  }, documentRef, urlRef);

  assert.equal(saved, true);
  assert.equal(anchor.download, "nexustrade-2026-08-17-campaign-a-abcdef12.xlsx");
  assert.deepEqual(clicks, ["clicked"]);
  assert.deepEqual(revoked, ["blob:nexus-export"]);
  assert.throws(() => saveNexusDownload({ filename: "empty.csv.zip", blob: new Blob([]) }, documentRef, urlRef), /vazio/i);
});

test("governance dialog traps tab focus and Escape returns control to its opener", () => {
  const focused = [];
  const controls = ["reason", "key", "cancel", "confirm"].map((id) => ({
    id,
    disabled: false,
    hidden: false,
    closest: () => null,
    focus: () => focused.push(id),
  }));
  const ownerDocument = { activeElement: controls[3] };
  const dialog = { ownerDocument, querySelectorAll: () => controls };
  const tabEvent = { key: "Tab", shiftKey: false, preventDefault() { this.prevented = true; } };

  assert.equal(handleGovernanceDialogKeydown(tabEvent, dialog), true);
  assert.equal(tabEvent.prevented, true);
  assert.deepEqual(focused, ["reason"]);

  let closed = false;
  const escapeEvent = { key: "Escape", preventDefault() { this.prevented = true; } };
  assert.equal(handleGovernanceDialogKeydown(escapeEvent, dialog, () => { closed = true; }), true);
  assert.equal(closed, true);
  assert.equal(escapeEvent.prevented, true);
});

import assert from "node:assert/strict";
import test from "node:test";

const storage = new Map([["nexus.dashboard.key", "dashboard-test-key"]]);
globalThis.localStorage = {
  getItem(key) { return storage.get(key) || ""; },
  setItem(key, value) { storage.set(key, value); },
};

const { createNexusTradeApi } = await import("../../static/js/nexus_trade_api.js");

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => data,
  };
}

test("snapshot uses the dashboard key only as a header", async () => {
  const calls = [];
  const api = createNexusTradeApi(async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({ status: "success", data: { snapshot_version: 7 } });
  });

  const snapshot = await api.snapshot();

  assert.equal(snapshot.snapshot_version, 7);
  assert.equal(calls[0].url, "/api/v1/nexus-trade");
  assert.equal(calls[0].options.headers["X-API-Key"], "dashboard-test-key");
  assert.equal(calls[0].url.includes("dashboard-test-key"), false);
});

test("governance credential is transient and sent only in its dedicated header", async () => {
  const calls = [];
  const api = createNexusTradeApi(async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({ status: "success", data: { transition: { outcome: "COMMITTED" } } });
  });
  const payload = {
    expected_revision: 9,
    request_id: "request-approve-9",
    reason: "revisão humana",
    reinforced_confirmation: false,
  };

  await api.approve("proposal-a", payload, "human-action-secret");

  const call = calls[0];
  assert.equal(call.url, "/api/v1/nexus-trade/proposals/proposal-a/approve");
  assert.equal(call.options.headers["X-Nexus-Human-Key"], "human-action-secret");
  assert.equal(call.options.headers["X-API-Key"], "dashboard-test-key");
  assert.equal(call.url.includes("human-action-secret"), false);
  assert.equal(call.options.body.includes("human-action-secret"), false);
  assert.equal([...storage.values()].includes("human-action-secret"), false);
});

test("structured 409 responses preserve status and readable reason", async () => {
  const api = createNexusTradeApi(async () => jsonResponse({ detail: "Champion precisa estar OFF" }, 409));

  await assert.rejects(
    api.rollback({
      target_version_id: "champion-v1",
      target_version_hash: "a".repeat(64),
      expected_revision: 4,
      request_id: "rollback-4",
      reason: "rollback manual",
    }, "human-key"),
    (error) => error.status === 409 && error.message === "Champion precisa estar OFF",
  );
});

test("report download preserves server filename mime and bytes", async () => {
  const bytes = new Uint8Array([80, 75, 3, 4]);
  const api = createNexusTradeApi(async () => ({
    ok: true,
    status: 200,
    headers: {
      get(name) {
        if (name.toLowerCase() === "content-type") return "application/zip";
        if (name.toLowerCase() === "content-disposition") return 'attachment; filename="nexus-week-32.zip"';
        return null;
      },
    },
    arrayBuffer: async () => bytes.buffer,
  }));

  const artifact = await api.downloadReport("weekly-32", "zip");

  assert.equal(artifact.filename, "nexus-week-32.zip");
  assert.equal(artifact.mediaType, "application/zip");
  assert.deepEqual([...new Uint8Array(await artifact.blob.arrayBuffer())], [...bytes]);
});

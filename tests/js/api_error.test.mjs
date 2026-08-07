import assert from "node:assert/strict";
import test from "node:test";

globalThis.localStorage = {
  getItem() { return ""; },
  setItem() {},
};

test("API validation errors are rendered as readable messages", async () => {
  globalThis.fetch = async () => ({
    ok: false,
    status: 422,
    json: async () => ({
      detail: [{ loc: ["body", "account_type"], msg: "Conta REAL inválida", type: "value_error" }],
    }),
  });

  const { api } = await import("../../static/js/api.js");

  await assert.rejects(api.bots(), (error) => {
    assert.equal(error.message, "Conta REAL inválida");
    assert.equal(error.status, 422);
    return true;
  });
});

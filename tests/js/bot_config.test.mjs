import assert from "node:assert/strict";
import test from "node:test";

import { configuredBotPayload } from "../../static/js/bot_config.js";

test("account changes always migrate legacy strategy parameters to the fixed profile", () => {
  const payload = configuredBotPayload(
    {
      name: "Donchian",
      symbol: "R_75",
      timeframe_seconds: 60,
      strategy_id: "donchian",
      strategy_config: { period: 21, deviation: 0, depth: 15, backstep: 3 },
      duration: 2,
      duration_unit: "m",
      initial_stake: 1,
      money_management: "fixed",
      money_config: {},
      risk_config: {},
    },
    { account_id: "ROT100", account_type: "real" },
  );

  assert.deepEqual(payload.strategy_config, {
    period: 21,
    deviation: 1,
    depth: 15,
    backstep: 3,
  });
  assert.equal(payload.account_id, "ROT100");
  assert.equal(payload.account_type, "real");
});

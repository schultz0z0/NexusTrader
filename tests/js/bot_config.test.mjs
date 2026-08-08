import assert from "node:assert/strict";
import test from "node:test";

import { configuredBotPayload, strategyProfile } from "../../static/js/bot_config.js";

test("Nexus defaults to ADX 30", () => {
  const config = strategyProfile("nexus_speed").strategy_config;
  assert.equal(config.adx_threshold, 30);
  assert.equal(config.touch_tolerance_bps, 0);
  assert.equal(config.touch_window_start_second, 1);
  assert.equal(config.touch_window_end_second, 30);
  assert.deepEqual(config.blocked_m5_candle_positions, [1, 5]);
});

test("Nexus preserves selected ADX threshold", () => {
  const profile = strategyProfile("nexus_speed", { adx_threshold: 25 });
  assert.equal(profile.strategy_config.adx_threshold, 25);
});

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

test("account changes preserve the fixed Nexus Speed five-tick profile", () => {
  const payload = configuredBotPayload(
    {
      name: "Nexus Speed",
      symbol: "R_100",
      timeframe_seconds: 60,
      strategy_id: "nexus_speed",
      strategy_config: { min_profit_ratio: 0.87, adx_threshold: 20 },
      duration: 5,
      duration_unit: "t",
      initial_stake: 1,
      money_management: "fixed",
      money_config: {},
      risk_config: {},
    },
    { account_id: "VRTC100", account_type: "demo" },
  );

  assert.equal(payload.strategy_id, "nexus_speed");
  assert.equal(payload.duration, 5);
  assert.equal(payload.duration_unit, "t");
  assert.equal(payload.strategy_config.ema_period, 5);
  assert.equal(payload.strategy_config.min_profit_ratio, 0.87);
  assert.equal(payload.strategy_config.adx_threshold, 20);
  assert.equal(payload.account_type, "demo");
});

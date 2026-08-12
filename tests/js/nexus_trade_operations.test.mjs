import assert from "node:assert/strict";
import test from "node:test";

import {
  buildNexusLiveModel,
  calculateBollingerHistory,
  nexusPositionPresentation,
} from "../../static/js/nexus_trade_operations.js";

test("Bollinger history is causal, warmup-aware and uses the configured sample deviation", () => {
  const points = Array.from({ length: 21 }, (_, index) => ({
    time: (index + 1) * 60,
    open: index + 1,
    high: index + 2,
    low: index,
    close: index + 1,
  }));
  const bands = calculateBollingerHistory(points, 20, 2);

  assert.equal(bands.middle.length, 2);
  assert.equal(bands.middle[0].time, 1200);
  assert.equal(bands.middle[0].value, 10.5);
  assert.ok(Math.abs(bands.upper[0].value - 22.332159566199232) < 1e-12);
  assert.ok(Math.abs(bands.lower[0].value - -1.3321595661992323) < 1e-12);
});

test("position presentation keeps live financial details and an honest expiry countdown", () => {
  const open = nexusPositionPresentation({
    stake: 0.35,
    buy_price: 0.35,
    entry_spot: 101.25,
    current_spot: 102.5,
    profit: 0.12,
    purchase_time: 1_000,
    date_expiry: 1_058,
  }, 1_000);
  assert.deepEqual(open, {
    stake: 0.35,
    entrySpot: 101.25,
    currentSpot: 102.5,
    profit: 0.12,
    outcomeLabel: "GANHANDO",
    purchaseTime: 1_000,
    expiryTime: 1_058,
    secondsRemaining: 58,
    countdown: "00:58",
    settlementPending: false,
  });

  const pending = nexusPositionPresentation({
    buy_price: 0.35,
    entry_spot: 101.25,
    current_spot: 101.1,
    profit: -0.35,
    date_expiry: 999,
  }, 1_000);
  assert.equal(pending.countdown, "Aguardando liquidação");
  assert.equal(pending.settlementPending, true);
  assert.equal(pending.stake, 0.35);

  const flat = nexusPositionPresentation({
    buy_price: 0.35,
    current_spot: 101.1,
    profit: 0,
  }, 1_000);
  assert.equal(flat.outcomeLabel, "EMPATANDO");

  const losing = nexusPositionPresentation({
    buy_price: 0.35,
    current_spot: 100.7,
    profit: -0.11,
  }, 1_000);
  assert.equal(losing.outcomeLabel, "PERDENDO");
});

test("live model filters lanes and exposes ADX, decisions, trades and active positions", () => {
  const state = {
    market: { symbol: "R_100", timeframe_seconds: 60, mode: "candles", points: [] },
    lastTick: { price: 100, epoch: 500 },
    positions: [
      { lane: "champion_baseline", contract_id: 1 },
      { lane: "challenger_trial", contract_id: 2 },
    ],
    decisions: [
      { lane: "challenger_trial", decision_id: "t", adx: 18, signal_epoch: 500 },
      { lane: "champion_baseline", decision_id: "c", adx: 21, signal_epoch: 499 },
    ],
    trades: [
      { lane: "champion_baseline", contract_id: 3 },
      { lane: "challenger_trial", contract_id: 4 },
    ],
    connection: { status: "live" },
  };

  const champion = buildNexusLiveModel(state, "champion_baseline");
  assert.equal(champion.latestAdx, 21);
  assert.deepEqual(champion.positions.map((item) => item.contract_id), [1]);
  assert.deepEqual(champion.trades.map((item) => item.contract_id), [3]);
  assert.equal(champion.connectionStatus, "live");

  const all = buildNexusLiveModel(state, "all");
  assert.equal(all.latestAdx, 18);
  assert.equal(all.positions.length, 2);
  assert.equal(all.decisions.length, 2);
});

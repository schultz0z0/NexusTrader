import test from "node:test";
import assert from "node:assert/strict";

class FakeSeries {
  constructor() { this.data = []; this.markerData = []; }
  setData(values) { this.data = [...values]; }
  update(value) {
    const index = this.data.findIndex((item) => item.time === value.time);
    if (index >= 0) this.data[index] = value;
    else this.data.push(value);
  }
  createPriceLine() { return {}; }
  removePriceLine() {}
  setMarkers(values) { this.markerData = [...values]; }
}

const removedSeries = [];

globalThis.ResizeObserver = class { observe() {} };
globalThis.LightweightCharts = {
  LineSeries: "line",
  CandlestickSeries: "candles",
  createChart() {
    return {
      addSeries() { return new FakeSeries(); },
      removeSeries(series) { removedSeries.push(series); },
      timeScale() { return { fitContent() {} }; },
      resize() {},
    };
  },
};

const { TradingChart } = await import("../../static/js/chart.js");

function newChart() {
  return new TradingChart({ clientWidth: 1000, clientHeight: 600 });
}

test("same-mode market switch removes old indicators and trade annotations", () => {
  const chart = newChart();
  chart.setHistory({ symbol: "1HZ75V", timeframe_seconds: 1, mode: "line", points: [{ time: 1, value: 7700 }] });
  chart.updateTick({ epoch: 2, price: 7710, bollinger: { upper: 7800, middle: 7700, lower: 7600 } });
  chart.showTrade({ contract_id: 10, contract_type: "CALL", entry_spot: 7710, purchase_time: 2 });

  chart.setHistory({ symbol: "R_75", timeframe_seconds: 1, mode: "line", points: [{ time: 100, value: 51000 }] });

  assert.deepEqual(chart.primary.data, [{ time: 100, value: 51000 }]);
  assert.deepEqual(chart.upper.data, []);
  assert.deepEqual(chart.middle.data, []);
  assert.deepEqual(chart.lower.data, []);
  assert.equal(chart.markers.length, 0);
});

test("contract updates keep one entry marker and one settlement marker", () => {
  const chart = newChart();
  chart.setHistory({ symbol: "R_75", timeframe_seconds: 1, mode: "line", points: [{ time: 100, value: 51000 }] });
  const open = { contract_id: 42, contract_type: "PUT", entry_spot: 51000, purchase_time: 100 };

  for (let index = 0; index < 20; index += 1) chart.showTrade({ ...open, profit: index / 10 });
  chart.closeTrade({ ...open, expiry_time: 110, profit: -1 });
  chart.closeTrade({ ...open, expiry_time: 110, profit: -1 });

  assert.equal(chart.markers.length, 2);
  assert.deepEqual(chart.markers.map((marker) => marker.text), ["PUT", "LOSS"]);
});

test("same-timestamp zigzag pivots are collapsed before Lightweight Charts", () => {
  const chart = newChart();
  chart.setHistory({
    bot_id: "bot-a",
    symbol: "R_75",
    timeframe_seconds: 60,
    mode: "candles",
    points: [{ time: 60, open: 10, high: 12, low: 9, close: 11 }],
    zigzag: [
      { time: 60, value: 12, type: "high" },
      { time: 60, value: 9, type: "low" },
      { time: 120, value: 13, type: "high" },
    ],
  });

  assert.deepEqual(chart.zigzag.data.map((point) => point.time), [60, 120]);
});

test("mode rebuild removes the previous zigzag series", () => {
  removedSeries.length = 0;
  const chart = newChart();
  chart.setHistory({ bot_id: "bot-a", symbol: "R_75", timeframe_seconds: 60, mode: "candles", points: [] });
  const previousZigzag = chart.zigzag;

  chart.setHistory({ bot_id: "bot-b", symbol: "R_75", timeframe_seconds: 60, mode: "candles", points: [] });

  assert.ok(removedSeries.includes(previousZigzag));
});

test("Nexus Speed history and ticks render only the EMA overlay", () => {
  const chart = newChart();
  chart.setHistory({
    bot_id: "nexus-a",
    symbol: "R_100",
    timeframe_seconds: 60,
    mode: "candles",
    indicator_mode: "ema",
    points: [{ time: 300, open: 99, high: 101, low: 98, close: 100 }],
    ema: [{ time: 300, value: 99.5 }],
  });

  chart.updateTick({
    epoch: 361,
    candle: { time: 360, open: 100, high: 102, low: 100, close: 101 },
    indicator_mode: "ema",
    ema: 100.0,
  });

  assert.deepEqual(chart.ema.data, [
    { time: 300, value: 99.5 },
    { time: 360, value: 100.0 },
  ]);
  assert.deepEqual(chart.upper.data, []);
  assert.deepEqual(chart.middle.data, []);
  assert.deepEqual(chart.lower.data, []);
  assert.deepEqual(chart.zigzag.data, []);
});

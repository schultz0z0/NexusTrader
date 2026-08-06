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

globalThis.ResizeObserver = class { observe() {} };
globalThis.LightweightCharts = {
  LineSeries: "line",
  CandlestickSeries: "candles",
  createChart() {
    return {
      addSeries() { return new FakeSeries(); },
      removeSeries() {},
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

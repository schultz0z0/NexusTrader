const COLORS = { grid: "#19222f", text: "#69768a", up: "#36d399", down: "#ff5364", line: "#20d4d0", band: "#8b5cf6", middle: "#4b87ff", ema: "#ff3b4f" };

function uniqueOrderedPoints(values) {
  const byTime = new Map();
  for (const point of values || []) {
    const time = Number(point?.time);
    if (Number.isFinite(time)) byTime.set(time, { ...point, time });
  }
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

export class TradingChart {
  constructor(container) {
    this.container = container;
    this.mode = null;
    this.contextKey = null;
    this.markers = [];
    this.markerMap = new Map();
    this.markerPrimitive = null;
    this.priceLine = null;
    this.chart = LightweightCharts.createChart(container, {
      width: container.clientWidth, height: container.clientHeight,
      layout: { background: { type: "solid", color: "#0b1018" }, textColor: COLORS.text, fontFamily: "JetBrains Mono" },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderColor: "#202a3a", scaleMargins: { top: .12, bottom: .12 } },
      timeScale: { borderColor: "#202a3a", timeVisible: true, secondsVisible: true, rightOffset: 8, barSpacing: 7 },
      crosshair: { mode: LightweightCharts.CrosshairMode?.Normal ?? 0, vertLine: { color: "#4c5b70", labelBackgroundColor: "#263345" }, horzLine: { color: "#4c5b70", labelBackgroundColor: "#263345" } },
      localization: { priceFormatter: (price) => Number(price).toFixed(price >= 100 ? 2 : 4) },
    });
    this.resizeObserver = new ResizeObserver(() => this.chart.resize(container.clientWidth, container.clientHeight));
    this.resizeObserver.observe(container);
  }

  addSeries(type, options) {
    if (this.chart.addSeries) return this.chart.addSeries(type, options);
    if (type === LightweightCharts.CandlestickSeries) return this.chart.addCandlestickSeries(options);
    return this.chart.addLineSeries(options);
  }

  ensureMode(mode, force = false) {
    if (this.mode === mode && !force) return;
    [this.primary, this.upper, this.middle, this.lower, this.zigzag, this.ema].filter(Boolean).forEach((series) => this.chart.removeSeries(series));
    this.mode = mode; this.markers = []; this.markerMap.clear(); this.markerPrimitive = null; this.priceLine = null;
    this.primary = mode === "line"
      ? this.addSeries(LightweightCharts.LineSeries, { color: COLORS.line, lineWidth: 2, crosshairMarkerRadius: 3, priceLineVisible: true })
      : this.addSeries(LightweightCharts.CandlestickSeries, { upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down });
    const bandOptions = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    this.upper = this.addSeries(LightweightCharts.LineSeries, { ...bandOptions, color: COLORS.band });
    this.middle = this.addSeries(LightweightCharts.LineSeries, { ...bandOptions, color: COLORS.middle, lineStyle: 2 });
    this.lower = this.addSeries(LightweightCharts.LineSeries, { ...bandOptions, color: COLORS.band });
    this.zigzag = this.addSeries(LightweightCharts.LineSeries, { color: "#f4bd50", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    this.ema = this.addSeries(LightweightCharts.LineSeries, { color: COLORS.ema, lineWidth: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
  }

  setHistory(market) {
    const mode = market?.mode || "candles";
    const indicatorMode = market?.indicator_mode || "donchian";
    const contextKey = `${market?.bot_id || ""}:${market?.symbol || ""}:${Number(market?.timeframe_seconds || 60)}:${mode}:${indicatorMode}`;
    const contextChanged = contextKey !== this.contextKey;
    this.ensureMode(mode, contextChanged);
    this.contextKey = contextKey;
    const points = (market?.points || []).filter((point) => Number.isFinite(point.time));
    this.primary.setData(points);
    const isEma = indicatorMode === "ema";
    const channel = market?.bollinger || market?.donchian || {};
    this.upper.setData(isEma ? [] : channel.upper || []);
    this.middle.setData(isEma ? [] : channel.middle || []);
    this.lower.setData(isEma ? [] : channel.lower || []);
    this.zigzag.setData(isEma || indicatorMode === "bollinger" ? [] : uniqueOrderedPoints(market?.zigzag));
    this.ema.setData(isEma ? uniqueOrderedPoints(market?.ema) : []);
    this.chart.timeScale().fitContent();
  }

  updateTick(event) {
    const point = this.mode === "line" ? { time: event.epoch, value: event.price } : event.candle;
    if (point?.time) this.primary.update(point);
    const bandTime = point?.time || event.epoch;
    if (event.indicator_mode === "ema") {
      if (event.ema != null) this.ema.update({ time: bandTime, value: event.ema });
      return;
    }
    const bb = event.bollinger || {};
    if (bb.upper != null) this.upper.update({ time: bandTime, value: bb.upper });
    if (bb.middle != null) this.middle.update({ time: bandTime, value: bb.middle });
    if (bb.lower != null) this.lower.update({ time: bandTime, value: bb.lower });
    if (event.zigzag) this.zigzag.setData(uniqueOrderedPoints(event.zigzag));
  }

  showTrade(trade) {
    if (this.priceLine) { this.primary.removePriceLine(this.priceLine); this.priceLine = null; }
    if (!trade || !this.primary) return;
    const price = Number(trade.entry_spot);
    if (Number.isFinite(price)) this.priceLine = this.primary.createPriceLine({ price, color: "#f4bd50", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: `ENTRY ${trade.contract_type || ""}` });
    if (trade.purchase_time) {
      this.upsertMarker(`entry:${trade.contract_id}`, { time: trade.purchase_time, position: trade.contract_type === "CALL" ? "belowBar" : "aboveBar", color: trade.contract_type === "CALL" ? COLORS.up : COLORS.down, shape: trade.contract_type === "CALL" ? "arrowUp" : "arrowDown", text: trade.contract_type });
      this.applyMarkers();
    }
  }

  closeTrade(trade) {
    if (trade?.expiry_time) this.upsertMarker(`exit:${trade.contract_id}`, { time: trade.expiry_time, position: "aboveBar", color: Number(trade.profit) >= 0 ? COLORS.up : COLORS.down, shape: "circle", text: Number(trade.profit) >= 0 ? "WIN" : "LOSS" });
    this.applyMarkers();
    this.showTrade(null);
  }

  upsertMarker(key, marker) {
    this.markerMap.set(key, marker);
    this.markers = [...this.markerMap.values()].sort((a, b) => a.time - b.time);
  }

  clearMarkers() {
    this.markers = [];
    this.markerMap.clear();
    this.applyMarkers();
    if (this.priceLine) {
      this.primary.removePriceLine(this.priceLine);
      this.priceLine = null;
    }
  }

  applyMarkers() {
    if (!this.primary) return;
    const ordered = [...this.markers].sort((a, b) => a.time - b.time);
    if (LightweightCharts.createSeriesMarkers) {
      if (!this.markerPrimitive) this.markerPrimitive = LightweightCharts.createSeriesMarkers(this.primary, ordered);
      else this.markerPrimitive.setMarkers(ordered);
    } else this.primary.setMarkers?.(ordered);
  }
}

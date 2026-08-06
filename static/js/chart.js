const COLORS = { grid: "#19222f", text: "#69768a", up: "#36d399", down: "#ff5364", line: "#20d4d0", band: "#8b5cf6", middle: "#4b87ff" };

export class TradingChart {
  constructor(container) {
    this.container = container;
    this.mode = null;
    this.markers = [];
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

  ensureMode(mode) {
    if (this.mode === mode) return;
    [this.primary, this.upper, this.middle, this.lower].filter(Boolean).forEach((series) => this.chart.removeSeries(series));
    this.mode = mode; this.markers = []; this.priceLine = null;
    this.primary = mode === "line"
      ? this.addSeries(LightweightCharts.LineSeries, { color: COLORS.line, lineWidth: 2, crosshairMarkerRadius: 3, priceLineVisible: true })
      : this.addSeries(LightweightCharts.CandlestickSeries, { upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down });
    const bandOptions = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    this.upper = this.addSeries(LightweightCharts.LineSeries, { ...bandOptions, color: COLORS.band });
    this.middle = this.addSeries(LightweightCharts.LineSeries, { ...bandOptions, color: COLORS.middle, lineStyle: 2 });
    this.lower = this.addSeries(LightweightCharts.LineSeries, { ...bandOptions, color: COLORS.band });
  }

  setHistory(market) {
    this.ensureMode(market?.mode || "candles");
    const points = (market?.points || []).filter((point) => Number.isFinite(point.time));
    this.primary.setData(points);
    this.chart.timeScale().fitContent();
  }

  updateTick(event) {
    const point = this.mode === "line" ? { time: event.epoch, value: event.price } : event.candle;
    if (point?.time) this.primary.update(point);
    const bb = event.bollinger || {};
    if (bb.upper != null) this.upper.update({ time: event.epoch, value: bb.upper });
    if (bb.middle != null) this.middle.update({ time: event.epoch, value: bb.middle });
    if (bb.lower != null) this.lower.update({ time: event.epoch, value: bb.lower });
  }

  showTrade(trade) {
    if (this.priceLine) { this.primary.removePriceLine(this.priceLine); this.priceLine = null; }
    if (!trade) return;
    const price = Number(trade.entry_spot);
    if (Number.isFinite(price)) this.priceLine = this.primary.createPriceLine({ price, color: "#f4bd50", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: `ENTRY ${trade.contract_type || ""}` });
    if (trade.purchase_time) {
      this.markers.push({ time: trade.purchase_time, position: trade.contract_type === "CALL" ? "belowBar" : "aboveBar", color: trade.contract_type === "CALL" ? COLORS.up : COLORS.down, shape: trade.contract_type === "CALL" ? "arrowUp" : "arrowDown", text: trade.contract_type });
      this.applyMarkers();
    }
  }

  closeTrade(trade) {
    if (trade?.expiry_time) this.markers.push({ time: trade.expiry_time, position: "aboveBar", color: Number(trade.profit) >= 0 ? COLORS.up : COLORS.down, shape: "circle", text: Number(trade.profit) >= 0 ? "WIN" : "LOSS" });
    this.applyMarkers();
    this.showTrade(null);
  }

  applyMarkers() {
    const ordered = [...this.markers].sort((a, b) => a.time - b.time);
    if (LightweightCharts.createSeriesMarkers) LightweightCharts.createSeriesMarkers(this.primary, ordered);
    else this.primary.setMarkers?.(ordered);
  }
}

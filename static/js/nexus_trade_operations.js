const VALID_LANES = new Set(["champion_baseline", "challenger_trial"]);

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function closeOf(point) {
  return finite(point?.close ?? point?.value);
}

export function calculateBollingerHistory(points = [], period = 20, deviation = 2) {
  const size = Number(period);
  const multiplier = Number(deviation);
  const result = { upper: [], middle: [], lower: [] };
  if (!Number.isInteger(size) || size < 2 || !Number.isFinite(multiplier) || multiplier <= 0) return result;
  const causal = [];
  for (const point of points) {
    const value = closeOf(point);
    const time = finite(point?.time);
    if (value === null || time === null) continue;
    causal.push({ time, value });
    if (causal.length < size) continue;
    const window = causal.slice(-size).map((item) => item.value);
    const middle = window.reduce((sum, item) => sum + item, 0) / size;
    const variance = window.reduce((sum, item) => sum + ((item - middle) ** 2), 0) / (size - 1);
    const width = Math.sqrt(variance) * multiplier;
    result.upper.push({ time, value: middle + width });
    result.middle.push({ time, value: middle });
    result.lower.push({ time, value: middle - width });
  }
  return result;
}

function forLane(items, lane) {
  if (lane === "all") return [...(items || [])];
  if (!VALID_LANES.has(lane)) return [];
  return (items || []).filter((item) => item?.lane === lane || item?.metadata?.lane === lane);
}

function newest(items) {
  return [...items].sort((left, right) => Number(
    right.signal_epoch ?? right.update_epoch ?? right.settled_epoch ?? 0,
  ) - Number(left.signal_epoch ?? left.update_epoch ?? left.settled_epoch ?? 0))[0] || null;
}

export function nexusMarketForChart(market) {
  if (!market || market.symbol !== "R_100" || Number(market.timeframe_seconds) !== 60) return null;
  return {
    ...market,
    indicator_mode: "bollinger",
    bollinger: calculateBollingerHistory(market.points || [], 20, 2),
    donchian: undefined,
    zigzag: [],
    ema: [],
  };
}

export function buildNexusLiveModel(state = {}, lane = "all") {
  const decisions = forLane(state.decisions, lane);
  const latestDecision = newest(decisions);
  return {
    lane,
    market: nexusMarketForChart(state.market),
    lastTick: state.lastTick || null,
    positions: forLane(state.positions, lane),
    decisions,
    trades: forLane(state.trades, lane),
    latestDecision,
    latestAdx: finite(latestDecision?.adx),
    connectionStatus: state.connection?.status || "idle",
  };
}

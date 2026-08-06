export class Store {
  constructor(initial = {}) { this.state = initial; this.listeners = new Set(); }
  get() { return this.state; }
  set(patch) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener(this.state));
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
}

export function marketMatchesBot(market, bot) {
  if (!market || !bot) return false;
  return market.symbol === bot.symbol
    && Number(market.timeframe_seconds) === Number(bot.timeframe_seconds);
}

export class Store {
  constructor(initial = {}) { this.state = initial; this.listeners = new Set(); }
  get() { return this.state; }
  set(patch) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener(this.state));
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
}

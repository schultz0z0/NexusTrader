import { api, ApiError, setApiKey, websocketUrl } from "./api.js";
import { Store } from "./store.js";
import { TradingChart } from "./chart.js";

const $ = (selector) => document.querySelector(selector);
const store = new Store({ bots: [], selectedId: null, snapshot: null, trades: [], connected: false });
const chart = new TradingChart($("#chart"));
let socket = null;
let socketToken = 0;
let reconnectTimer = null;
let countdownTimer = null;
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD" });
const price = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(Number(value) >= 100 ? 2 : 4) : "—";

function toast(message, type = "info") {
  const node = document.createElement("div"); node.className = `toast ${type}`; node.textContent = message;
  $("#toast-region").append(node); setTimeout(() => node.remove(), 4200);
}

function selectedBot() { return store.get().bots.find((bot) => bot.id === store.get().selectedId); }

function renderBots() {
  const { bots, selectedId } = store.get();
  $("#bot-list").innerHTML = bots.map((bot) => `<button class="bot-item ${bot.id === selectedId ? "active" : ""} ${String(bot.runtime_state).toLowerCase()}" data-bot-id="${bot.id}"><span class="bot-dot"></span><span class="bot-copy"><strong>${escapeHtml(bot.name)}</strong><small>${escapeHtml(bot.symbol)} · ${timeframe(bot.timeframe_seconds)}</small></span><small>${statusLabel(bot.runtime_state)}</small></button>`).join("");
}

function renderHeader() {
  const bot = selectedBot();
  $("#market-symbol").textContent = bot?.symbol || "—";
  $("#market-name").textContent = bot ? "Deriv Synthetic Index" : "Selecione um robô";
  $("#timeframe-label").textContent = bot ? timeframe(bot.timeframe_seconds) : "—";
  $("#selected-bot-name").textContent = bot?.name || "Nenhum robô";
  $("#selected-bot-strategy").textContent = bot ? "Bollinger Mean Reversion" : "—";
  const running = bot?.desired_state === "RUNNING";
  const button = $("#toggle-bot"); button.disabled = !bot; button.textContent = running ? "PARAR ROBÔ" : "INICIAR ROBÔ"; button.classList.toggle("stop", running);
  const risk = bot?.risk_config || {};
  $("#metric-target").textContent = money.format(Number(risk.take_profit_daily || 0));
  $("#metric-stop").textContent = money.format(-Math.abs(Number(risk.stop_loss_daily || 0)));
}

function renderSnapshot() {
  const { snapshot } = store.get();
  if (!snapshot) return;
  chart.setHistory(snapshot.market);
  $("#chart-state").hidden = Boolean(snapshot.market?.points?.length);
  if (snapshot.last_tick) updateMarket(snapshot.last_tick);
  renderActiveTrade(snapshot.active_trade);
  renderTrades(snapshot.recent_trades?.length ? snapshot.recent_trades : store.get().trades);
}

function updateMarket(event) {
  chart.updateTick(event);
  $("#live-price").textContent = price(event.price);
  $("#price-change").textContent = `Tick ${new Date(event.epoch * 1000).toLocaleTimeString("pt-BR")}`;
  $("#chart-state").hidden = true;
}

function renderActiveTrade(trade) {
  clearInterval(countdownTimer); countdownTimer = null;
  const box = $("#active-trade"); const chip = $("#operation-state");
  if (!trade) {
    box.className = "active-trade empty"; box.innerHTML = `<div class="radar"><i></i><i></i><b></b></div><strong>Nenhuma posição aberta</strong><p>O contrato aparecerá aqui desde a compra até a liquidação.</p>`;
    chip.className = "status-chip neutral"; chip.textContent = "SEM POSIÇÃO"; chart.showTrade(null); return;
  }
  const pnl = Number(trade.profit || 0); chip.className = "status-chip live"; chip.textContent = "AO VIVO";
  box.className = "active-trade";
  box.innerHTML = `<div class="trade-live-head"><span class="direction ${String(trade.contract_type).toLowerCase()}">${escapeHtml(trade.contract_type || "—")}</span><span class="contract-id">#${escapeHtml(trade.contract_id)}</span></div><div class="live-pnl"><span>P&amp;L FLUTUANTE</span><strong class="${pnl >= 0 ? "positive" : "negative"}">${money.format(pnl)}</strong></div><div class="live-details"><div><span>Entrada</span><strong>${price(trade.entry_spot)}</strong></div><div><span>Spot atual</span><strong>${price(trade.exit_spot)}</strong></div><div><span>Stake</span><strong>${money.format(Number(trade.stake || 0))}</strong></div><div><span>Expira em</span><strong id="trade-countdown">—</strong></div></div>`;
  chart.showTrade(trade); updateCountdown(trade.expiry_time); countdownTimer = setInterval(() => updateCountdown(trade.expiry_time), 1000);
}

function updateCountdown(expiry) {
  const node = $("#trade-countdown"); if (!node) return;
  const remaining = Math.max(0, Number(expiry || 0) - Math.floor(Date.now() / 1000)); node.textContent = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
}

function renderTrades(trades = []) {
  const rows = trades.filter((item) => item.status !== "open"); const wins = rows.filter((item) => Number(item.profit) > 0).length; const pnl = rows.reduce((sum, item) => sum + Number(item.profit || 0), 0);
  $("#trade-count").textContent = `${rows.length} operações`; $("#win-count").textContent = `${wins} wins`; $("#loss-count").textContent = `${rows.length - wins} losses`;
  $("#metric-pnl").textContent = money.format(pnl); $("#metric-pnl").className = pnl >= 0 ? "positive" : "negative"; $("#metric-winrate").textContent = rows.length ? `${Math.round(wins / rows.length * 100)}%` : "0%";
  const target = Number(selectedBot()?.risk_config?.take_profit_daily || 0); const progress = target ? Math.max(0, Math.min(100, pnl / target * 100)) : 0; $("#risk-progress-bar").style.width = `${progress}%`; $("#risk-progress-text").textContent = `${Math.round(progress)}% da meta`;
  $("#trade-history").innerHTML = rows.length ? rows.map((item) => `<tr><td>${formatTime(item.expiry_time || item.created_at)}</td><td>${escapeHtml(item.symbol || "—")}</td><td><span class="direction ${String(item.contract_type).toLowerCase()}">${escapeHtml(item.contract_type || "—")}</span></td><td>${price(item.entry_spot)}</td><td>${price(item.exit_spot)}</td><td>${money.format(Number(item.stake || 0))}</td><td class="${Number(item.profit) >= 0 ? "positive" : "negative"}">${Number(item.profit) >= 0 ? "WIN" : "LOSS"}</td><td class="${Number(item.profit) >= 0 ? "positive" : "negative"}">${money.format(Number(item.profit || 0))}</td></tr>`).join("") : `<tr class="empty-row"><td colspan="8">As operações encerradas aparecerão aqui em tempo real.</td></tr>`;
}

async function load() {
  try {
    const bots = await api.bots(); const current = store.get().selectedId; store.set({ bots, selectedId: bots.some((b) => b.id === current) ? current : bots[0]?.id || null });
    renderBots(); renderHeader(); if (store.get().selectedId) await selectBot(store.get().selectedId);
  } catch (error) { handleError(error); }
}

async function selectBot(id) {
  socketToken += 1; if (socket) socket.close(); clearTimeout(reconnectTimer);
  store.set({ selectedId: id, connected: false }); renderBots(); renderHeader(); setConnection(false, "Conectando");
  try {
    const [snapshot, trades] = await Promise.all([api.snapshot(id), api.trades(id)]); store.set({ snapshot, trades }); renderSnapshot(); connectLive(id, socketToken);
  } catch (error) { handleError(error); }
}

function connectLive(botId, token) {
  socket = new WebSocket(websocketUrl(botId));
  socket.onopen = () => { if (token === socketToken) { setConnection(true, "Tempo real"); socket.send("ready"); } };
  socket.onmessage = ({ data }) => { if (token !== socketToken) return; const message = JSON.parse(data); if (message.type === "snapshot") { store.set({ snapshot: message.data }); renderSnapshot(); } else applyEvent(message); };
  socket.onclose = () => { if (token === socketToken) { setConnection(false, "Reconectando"); reconnectTimer = setTimeout(() => connectLive(botId, token), 1800); } };
}

function applyEvent(event) {
  const snapshot = store.get().snapshot || {};
  if (event.type === "market.history") { snapshot.market = event; chart.setHistory(event); $("#chart-state").hidden = !(event.points || []).length; }
  if (event.type === "market.tick") { snapshot.last_tick = event; updateMarket(event); }
  if (["trade.opened", "trade.updated"].includes(event.type)) { snapshot.active_trade = event.trade; renderActiveTrade(event.trade); }
  if (event.type === "trade.closed") { snapshot.active_trade = null; snapshot.recent_trades = [event.trade, ...(snapshot.recent_trades || []).filter((t) => t.contract_id !== event.trade.contract_id)]; chart.closeTrade(event.trade); renderActiveTrade(null); renderTrades(snapshot.recent_trades); toast(`Contrato #${event.trade.contract_id}: ${money.format(Number(event.trade.profit || 0))}`); }
  if (event.type === "runtime.status") { const bot = selectedBot(); if (bot) bot.runtime_state = event.status; renderBots(); renderHeader(); }
  store.set({ snapshot });
}

function setConnection(online, label) { const node = $("#connection-status"); node.classList.toggle("is-online", online); node.classList.toggle("is-offline", !online); node.querySelector("span").textContent = label; }

function openDrawer(isNew = false) {
  const form = $("#config-form"); form.reset(); const bot = isNew ? null : selectedBot(); $("#config-id").value = bot?.id || ""; $("#config-title").textContent = bot ? `Editar ${bot.name}` : "Novo robô";
  if (bot) fillForm(form, bot); $("#bot-config").classList.add("open"); $("#bot-config").setAttribute("aria-hidden", "false"); $("#drawer-backdrop").hidden = false;
}
function closeDrawer() { $("#bot-config").classList.remove("open"); $("#bot-config").setAttribute("aria-hidden", "true"); $("#drawer-backdrop").hidden = true; $("#form-error").hidden = true; }
function fillForm(form, bot) { const values = { ...bot, ...(bot.strategy_config || {}), ...(bot.money_config || {}), ...(bot.risk_config || {}) }; Object.entries(values).forEach(([key, value]) => { if (form.elements[key] && typeof value !== "object") form.elements[key].value = value; }); }
function formPayload(form) { const data = Object.fromEntries(new FormData(form)); return { name: data.name, account_id: data.account_id, account_type: "demo", symbol: data.symbol, timeframe_seconds: Number(data.timeframe_seconds), strategy_id: data.strategy_id, strategy_config: { period: Number(data.period), std_dev: Number(data.std_dev) }, duration: Number(data.duration), duration_unit: data.duration_unit, initial_stake: Number(data.initial_stake), money_management: data.money_management, money_config: { multiplier: Number(data.multiplier), max_levels: Number(data.max_levels) }, risk_config: { take_profit_daily: Number(data.take_profit_daily), stop_loss_daily: Number(data.stop_loss_daily), max_daily_trades: Number(data.max_daily_trades), max_single_stake: Number(data.max_single_stake), max_consecutive_losses: Number(data.max_consecutive_losses), cooldown_minutes: Number(data.cooldown_minutes) } }; }

$("#bot-list").addEventListener("click", (event) => { const button = event.target.closest("[data-bot-id]"); if (button) selectBot(button.dataset.botId); });
$("#open-config").addEventListener("click", () => openDrawer(false)); $("#new-bot").addEventListener("click", () => openDrawer(true)); $("#close-config").addEventListener("click", closeDrawer); $("#cancel-config").addEventListener("click", closeDrawer); $("#drawer-backdrop").addEventListener("click", closeDrawer);
$("#config-form").addEventListener("submit", async (event) => { event.preventDefault(); const id = $("#config-id").value; const errorNode = $("#form-error"); try { const saved = id ? await api.updateBot(id, formPayload(event.currentTarget)) : await api.createBot(formPayload(event.currentTarget)); closeDrawer(); await load(); await selectBot(saved.id); toast("Configuração salva com sucesso."); } catch (error) { errorNode.textContent = error.message; errorNode.hidden = false; } });
$("#toggle-bot").addEventListener("click", async () => { const bot = selectedBot(); if (!bot) return; try { const updated = bot.desired_state === "RUNNING" ? await api.stopBot(bot.id) : await api.startBot(bot.id); Object.assign(bot, updated); renderBots(); renderHeader(); toast(updated.desired_state === "RUNNING" ? "Comando de início enviado." : "Parada segura solicitada."); } catch (error) { handleError(error); } });
$("#stop-all").addEventListener("click", async () => { const running = store.get().bots.filter((bot) => bot.desired_state === "RUNNING"); await Promise.allSettled(running.map((bot) => api.stopBot(bot.id))); await load(); toast(`${running.length} robô(s) receberam parada segura.`); });
$("#auth-form").addEventListener("submit", async (event) => { event.preventDefault(); setApiKey($("#api-key").value); $("#auth-error").textContent = ""; $("#auth-gate").hidden = true; await load(); });

function handleError(error) { if (error instanceof ApiError && error.status === 401) { $("#auth-gate").hidden = false; $("#auth-error").textContent = "Chave obrigatória ou inválida."; } else toast(error.message || "Erro inesperado", "error"); }
function timeframe(seconds) { return Number(seconds) <= 1 ? "1s" : Number(seconds) === 300 ? "5m" : "1m"; }
function statusLabel(status) { return ({ RUNNING: "LIVE", STARTING: "START", STOPPING: "STOP", ERROR: "ERRO", STOPPED: "OFF" })[status] || "OFF"; }
function formatTime(value) { if (!value) return "—"; const date = typeof value === "number" ? new Date(value * 1000) : new Date(value); return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }

load();

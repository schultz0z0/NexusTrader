import { api, ApiError, setApiKey, websocketUrl } from "./api.js";
import { Store, marketMatchesBot } from "./store.js";
import { TradingChart } from "./chart.js";
import { contractPresentation } from "./trade_state.js";
import { configuredBotPayload, strategyProfile } from "./bot_config.js";

const $ = (selector) => document.querySelector(selector);
const ACCOUNT_STORAGE_KEY = "nexus.global.account";
const store = new Store({ bots: [], accounts: [], selectedId: null, snapshot: null, trades: [], connected: false });
const chart = new TradingChart($("#chart"));
let socket = null;
let socketToken = 0;
let reconnectTimer = null;
let countdownTimer = null;
let realConfirmationResolver = null;
let activeAccountId = localStorage.getItem(ACCOUNT_STORAGE_KEY) || "";
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD" });
const price = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(Number(value) >= 100 ? 2 : 4) : "—";

function toast(message, type = "info") {
  const node = document.createElement("div"); node.className = `toast ${type}`; node.textContent = message;
  $("#toast-region").append(node); setTimeout(() => node.remove(), 4200);
}

function selectedBot() { return store.get().bots.find((bot) => bot.id === store.get().selectedId); }
function activeAccount() { return store.get().accounts.find((account) => account.account_id === activeAccountId) || null; }

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
  const isDonchian = bot?.strategy_id === "donchian";
  const isNexusSpeed = bot?.strategy_id === "nexus_speed";
  $("#selected-bot-strategy").textContent = bot ? (isNexusSpeed ? "Nexus Speed" : "Donchian + ZigZag") : "—";
  $("#legend-upper").textContent = isDonchian ? "Donchian Upper" : "";
  $("#legend-mid").textContent = isNexusSpeed ? "EMA(5)" : "Donchian Middle";
  $("#legend-lower").textContent = isDonchian ? "Donchian Lower" : "";
  const running = bot?.desired_state === "RUNNING";
  const button = $("#toggle-bot"); button.disabled = !bot; button.textContent = running ? "PARAR ROBÔ" : "INICIAR ROBÔ"; button.classList.toggle("stop", running);
  const risk = bot?.risk_config || {};
  $("#metric-target").textContent = money.format(Number(risk.take_profit_daily || 0));
  $("#metric-stop").textContent = money.format(-Math.abs(Number(risk.stop_loss_daily || 0)));
  renderAccountMode();
}

function renderAccountMode() {
  const account = activeAccount();
  const real = account?.account_type === "real";
  const badge = $("#environment-badge");
  badge.classList.toggle("real", real);
  badge.querySelector("span:last-child").textContent = real ? "CONTA REAL" : "AMBIENTE DEMO";
  $("#account-mode-card").classList.toggle("real", real);
  $("#account-shield").textContent = real ? "R" : "D";
  $("#account-mode-title").textContent = real ? "Conta real" : "Conta demo";
  $("#account-mode-detail").textContent = account?.account_id
    ? `${account.account_id} · ${real ? "capital real" : "saldo virtual"}`
    : "Selecione uma conta";
}

function renderSnapshot() {
  const { snapshot } = store.get();
  const bot = selectedBot();
  if (!snapshot) {
    if (bot) {
      chart.setHistory({ bot_id: bot.id, symbol: bot.symbol, timeframe_seconds: bot.timeframe_seconds, mode: Number(bot.timeframe_seconds) <= 1 ? "line" : "candles", points: [] });
      showChartState("Trocando mercado", `Aguardando o histórico de ${bot.symbol} · ${timeframe(bot.timeframe_seconds)}.`);
    }
    renderActiveTrade(null);
    renderTrades([]);
    return;
  }
  if (marketMatchesBot(snapshot.market, bot)) {
    chart.setHistory(snapshot.market);
    $("#chart-state").hidden = Boolean(snapshot.market?.points?.length);
    if (snapshot.last_tick) updateMarket(snapshot.last_tick);
  } else if (bot) {
    chart.setHistory({
      bot_id: bot.id,
      symbol: bot.symbol,
      timeframe_seconds: bot.timeframe_seconds,
      mode: Number(bot.timeframe_seconds) <= 1 ? "line" : "candles",
      points: [],
    });
    showChartState("Trocando mercado", `Aguardando o histórico de ${bot.symbol} · ${timeframe(bot.timeframe_seconds)}.`);
  }
  renderActiveTrade(snapshot.active_trade);
  renderTrades(store.get().trades || []);
}

function updateMarket(event) {
  if (!marketMatchesBot(event, selectedBot())) return;
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
  const pnl = Number(trade.profit || 0);
  box.className = "active-trade";
  box.innerHTML = `<div class="trade-live-head"><span class="direction ${String(trade.contract_type).toLowerCase()}">${escapeHtml(trade.contract_type || "—")}</span><span class="contract-id">#${escapeHtml(trade.contract_id)}</span></div><div class="live-pnl"><span id="trade-pnl-label">P&amp;L FLUTUANTE</span><strong class="${pnl >= 0 ? "positive" : "negative"}">${money.format(pnl)}</strong></div><div class="live-details"><div><span>Entrada</span><strong>${price(trade.entry_spot)}</strong></div><div><span>Spot atual</span><strong>${price(trade.exit_spot)}</strong></div><div><span>Stake</span><strong>${money.format(Number(trade.stake || 0))}</strong></div><div><span id="trade-countdown-label">Expira em</span><strong id="trade-countdown">—</strong></div></div>`;
  chart.showTrade(trade); updateActiveTradePresentation(trade); countdownTimer = setInterval(() => updateActiveTradePresentation(trade), 1000);
}

function updateActiveTradePresentation(trade) {
  const view = contractPresentation(trade);
  if (!view) return;
  const chip = $("#operation-state");
  chip.className = `status-chip ${view.state === "live" ? "live" : "waiting"}`;
  chip.textContent = view.chip;
  $("#trade-pnl-label").textContent = view.pnlLabel;
  $("#trade-countdown-label").textContent = view.countdownLabel;
  $("#trade-countdown").textContent = view.countdown;
}

function renderTrades(trades = []) {
  const today = new Date();
  const rows = trades.filter((item) => {
    if (item.status === "open") return false;
    const epoch = item.expiry_time || item.purchase_time;
    if (!epoch) return true;
    const d = new Date(epoch * 1000);
    return d.getDate() === today.getDate() && d.getMonth() === today.getMonth() && d.getFullYear() === today.getFullYear();
  });
  const wins = rows.filter((item) => Number(item.profit) > 0).length; const pnl = rows.reduce((sum, item) => sum + Number(item.profit || 0), 0);
  $("#trade-count").textContent = `${rows.length} operações`; $("#win-count").textContent = `${wins} wins`; $("#loss-count").textContent = `${rows.length - wins} losses`;
  $("#metric-pnl").textContent = money.format(pnl); $("#metric-pnl").className = pnl >= 0 ? "positive" : "negative"; $("#metric-winrate").textContent = rows.length ? `${Math.round(wins / rows.length * 100)}%` : "0%";
  const target = Number(selectedBot()?.risk_config?.take_profit_daily || 0); const progress = target ? Math.max(0, Math.min(100, pnl / target * 100)) : 0; $("#risk-progress-bar").style.width = `${progress}%`; $("#risk-progress-text").textContent = `${Math.round(progress)}% da meta`;
  $("#trade-history").innerHTML = rows.length ? rows.map((item) => `<tr><td>${formatTime(item.expiry_time || item.created_at)}</td><td>${escapeHtml(item.symbol || "—")}</td><td><span class="direction ${String(item.contract_type).toLowerCase()}">${escapeHtml(item.contract_type || "—")}</span></td><td>${price(item.entry_spot)}</td><td>${price(item.exit_spot)}</td><td>${money.format(Number(item.stake || 0))}</td><td class="${Number(item.profit) >= 0 ? "positive" : "negative"}">${Number(item.profit) >= 0 ? "WIN" : "LOSS"}</td><td class="${Number(item.profit) >= 0 ? "positive" : "negative"}">${money.format(Number(item.profit || 0))}</td></tr>`).join("") : `<tr class="empty-row"><td colspan="8">As operações de hoje aparecerão aqui.</td></tr>`;
}

async function load(preferredId = null) {
  try {
    const bots = await api.bots();
    let accounts = store.get().accounts;
    try { accounts = await api.accounts(); } catch (error) { if (error instanceof ApiError && error.status === 401) throw error; toast(`Contas Deriv indisponíveis: ${error.message}`, "error"); }
    const current = preferredId || store.get().selectedId; store.set({ bots, accounts, selectedId: bots.some((b) => b.id === current) ? current : bots[0]?.id || null });
    const remembered = accounts.find((account) => account.account_id === activeAccountId);
    const configured = accounts.find((account) => account.account_id === bots.find((bot) => bot.id === store.get().selectedId)?.account_id);
    const fallback = accounts.find((account) => account.account_type === "demo") || accounts[0];
    activeAccountId = (remembered || configured || fallback)?.account_id || "";
    populateAccountSelect(activeAccountId); syncAccountSelection(false);
    renderBots(); renderHeader(); if (store.get().selectedId) await selectBot(store.get().selectedId);
  } catch (error) { handleError(error); }
}

async function refreshAccounts() {
  try {
    const accounts = await api.accounts();
    store.set({ accounts });
    populateAccountSelect(activeAccountId);
    syncAccountSelection(false);
  } catch { /* silent — balance will update on next full load */ }
}

async function selectBot(id) {
  const token = ++socketToken; if (socket) socket.close(); clearTimeout(reconnectTimer);
  store.set({ selectedId: id, connected: false, snapshot: null, trades: [] }); renderBots(); renderHeader(); renderSnapshot(); setConnection(false, "Conectando");
  connectLive(id, token);
  try {
    const [fetchedSnapshot, fetchedTrades] = await Promise.all([api.snapshot(id), api.trades(id)]);
    if (token !== socketToken) return;
    const currentSnapshot = store.get().snapshot;
    const snapshot = Number(currentSnapshot?.last_event_epoch || 0) > Number(fetchedSnapshot?.last_event_epoch || 0)
      ? currentSnapshot : fetchedSnapshot;
    const liveTrades = store.get().trades || [];
    const recentTrades = snapshot?.recent_trades || [];
    const trades = [...liveTrades, ...recentTrades, ...fetchedTrades].filter(
      (trade, index, all) => all.findIndex((candidate) => candidate.contract_id === trade.contract_id) === index
    );
    store.set({ snapshot, trades }); renderSnapshot();
  } catch (error) { handleError(error); }
}

async function connectLive(botId, token) {
  try {
    const { ticket } = await api.wsTicket(botId);
    if (token !== socketToken) return;
    const activeSocket = new WebSocket(websocketUrl(botId, ticket));
    socket = activeSocket;
    activeSocket.onopen = () => { if (token === socketToken) { setConnection(true, "Tempo real"); activeSocket.send("ready"); } };
    activeSocket.onmessage = ({ data }) => { if (token !== socketToken) return; const message = JSON.parse(data); if (message.type === "snapshot") { store.set({ snapshot: message.data }); renderSnapshot(); } else applyEvent(message); };
    activeSocket.onclose = () => { if (token === socketToken) { setConnection(false, "Reconectando"); reconnectTimer = setTimeout(() => connectLive(botId, token), 1800); } };
  } catch (error) {
    if (token === socketToken) {
      setConnection(false, "Reconectando");
      reconnectTimer = setTimeout(() => connectLive(botId, token), 1800);
    }
  }
}

function applyEvent(event) {
  if (event.bot_id !== store.get().selectedId) return;
  const snapshot = store.get().snapshot || {};
  if (event.type === "market.history" && marketMatchesBot(event, selectedBot())) { snapshot.market = event; snapshot.last_tick = null; chart.setHistory(event); $("#chart-state").hidden = !(event.points || []).length; }
  if (event.type === "market.tick" && marketMatchesBot(event, selectedBot())) { snapshot.last_tick = event; updateMarket(event); }
  if (["trade.opened", "trade.updated"].includes(event.type)) { snapshot.active_trade = event.trade; renderActiveTrade(event.trade); }
  if (event.type === "trade.closed") { 
    const currentTrades = store.get().trades || []; 
    const updatedTrades = [event.trade, ...currentTrades.filter((t) => t.contract_id !== event.trade.contract_id)];
    store.set({ trades: updatedTrades });
    snapshot.active_trade = null; 
    chart.closeTrade(event.trade); 
    renderActiveTrade(null); 
    renderTrades(updatedTrades); 
    toast(`Contrato #${event.trade.contract_id}: ${money.format(Number(event.trade.profit || 0))}`); 
    refreshAccounts(); 
  }
  if (event.type === "runtime.status") { 
    const bot = selectedBot(); if (bot) bot.runtime_state = event.status; renderBots(); renderHeader(); 
  }
  if (event.type === "risk.blocked") {
    const detail = event.reason === "ownership_quarantine"
      ? "Compra com ownership pendente: novas ordens estão bloqueadas."
      : `Operação bloqueada pelo risco: ${event.reason || "limite operacional"}.`;
    toast(detail, "error");
  }
  store.set({ snapshot });
}

function setConnection(online, label) { const node = $("#connection-status"); node.classList.toggle("is-online", online); node.classList.toggle("is-offline", !online); node.querySelector("span").textContent = label; }

function showChartState(title, detail) {
  const state = $("#chart-state");
  state.querySelector("strong").textContent = title;
  state.querySelector("small").textContent = detail;
  state.hidden = false;
}

function openDrawer(isNew = false) {
  const form = $("#config-form"); form.reset(); const bot = isNew ? null : selectedBot(); $("#config-id").value = bot?.id || ""; $("#config-title").textContent = bot ? `Editar ${bot.name}` : "Novo robô";
  if (bot) fillForm(form, bot); 
  $("#strategy-select")?.dispatchEvent(new Event("change"));
  $("#bot-config").classList.add("open"); $("#bot-config").setAttribute("aria-hidden", "false"); $("#drawer-backdrop").hidden = false;
}
function closeDrawer() { $("#bot-config").classList.remove("open"); $("#bot-config").setAttribute("aria-hidden", "true"); $("#drawer-backdrop").hidden = true; $("#form-error").hidden = true; }
function fillForm(form, bot) { const values = { ...bot, ...(bot.strategy_config || {}), ...(bot.money_config || {}), ...(bot.risk_config || {}) }; Object.entries(values).forEach(([key, value]) => { if (form.elements[key] && typeof value !== "object") form.elements[key].value = value; }); }
function populateAccountSelect(preferredId = "") {
  const select = $("#account-select");
  const accounts = store.get().accounts || [];
  select.innerHTML = accounts.length
    ? accounts.map((account) => `<option value="${escapeHtml(account.account_id)}" data-account-type="${escapeHtml(account.account_type)}" data-balance="${Number(account.balance || 0)}" data-currency="${escapeHtml(account.currency || "USD")}">${account.account_type === "real" ? "REAL" : "DEMO"} · ${escapeHtml(account.account_id)} · ${money.format(Number(account.balance || 0))}</option>`).join("")
    : `<option value="">Nenhuma conta disponível</option>`;
  if (preferredId && accounts.some((account) => account.account_id === preferredId)) select.value = preferredId;
}
function syncAccountSelection(persist = true) {
  const option = $("#account-select").selectedOptions[0];
  const type = option?.dataset.accountType || "demo";
  activeAccountId = option?.value || "";
  if (persist && activeAccountId) localStorage.setItem(ACCOUNT_STORAGE_KEY, activeAccountId);
  $("#account-type").value = type;
  const summary = $("#account-summary");
  summary.classList.toggle("real", type === "real");
  summary.textContent = option?.value
    ? `${type === "real" ? "REAL" : "DEMO"} · ${option.dataset.currency || "USD"} ${Number(option.dataset.balance || 0).toFixed(2)}`
    : "Não foi possível consultar as contas autorizadas pelo token Deriv.";
  renderAccountMode();
}
function formPayload(form) { const data = Object.fromEntries(new FormData(form)); const account = activeAccount(); if (!account) throw new Error("Selecione uma conta global Deriv antes de salvar."); return { name: data.name, account_id: account.account_id, account_type: account.account_type, symbol: data.symbol, ...strategyProfile(data.strategy_id), initial_stake: Number(data.initial_stake), money_management: data.money_management, money_config: { multiplier: Number(data.multiplier), max_levels: Number(data.max_levels) }, risk_config: { take_profit_daily: Number(data.take_profit_daily), stop_loss_daily: Number(data.stop_loss_daily), max_daily_trades: Number(data.max_daily_trades), max_single_stake: Number(data.max_single_stake), max_consecutive_losses: Number(data.max_consecutive_losses), cooldown_minutes: Number(data.cooldown_minutes) } }; }

async function changeGlobalAccount() {
  const select = $("#account-select");
  const previousId = activeAccountId;
  const running = store.get().bots.some((bot) => bot.desired_state === "RUNNING" || ["STARTING", "RUNNING", "STOPPING"].includes(bot.runtime_state));
  if (running) { select.value = previousId; toast("Pare todos os robôs antes de trocar a conta global.", "error"); return; }
  syncAccountSelection();
  const account = activeAccount();
  if (!account) return;
  select.disabled = true;
  try {
    const updatedBots = await Promise.all(store.get().bots.map((bot) =>
      bot.account_id === account.account_id && bot.account_type === account.account_type
        ? Promise.resolve(bot)
        : api.updateBot(bot.id, configuredBotPayload(bot, account))
    ));
    store.set({ bots: updatedBots }); renderBots(); renderHeader();
    toast(`Conta global alterada para ${account.account_type === "real" ? "REAL" : "DEMO"} · ${account.account_id}.`);
  } catch (error) {
    activeAccountId = previousId; select.value = previousId; syncAccountSelection(false); handleError(error); await load();
  } finally { select.disabled = false; }
}

function confirmRealStart(bot, account) {
  $("#real-confirm-account").textContent = account.account_id;
  $("#real-confirm-bot").textContent = bot.name;
  $("#real-confirm-instruction").textContent = `REAL ${account.account_id}`;
  $("#real-confirm-phrase").value = "";
  $("#real-confirm-error").hidden = true;
  $("#real-account-dialog").hidden = false;
  setTimeout(() => $("#real-confirm-phrase").focus(), 0);
  return new Promise((resolve) => { realConfirmationResolver = resolve; });
}
function closeRealConfirmation(value) {
  $("#real-account-dialog").hidden = true;
  const resolve = realConfirmationResolver; realConfirmationResolver = null;
  if (resolve) resolve(value);
}

$("#bot-list").addEventListener("click", (event) => { const button = event.target.closest("[data-bot-id]"); if (button) selectBot(button.dataset.botId); });
$("#open-config").addEventListener("click", () => openDrawer(false)); $("#new-bot").addEventListener("click", () => openDrawer(true)); $("#close-config").addEventListener("click", closeDrawer); $("#cancel-config").addEventListener("click", closeDrawer); $("#drawer-backdrop").addEventListener("click", closeDrawer);
$("#account-select").addEventListener("change", changeGlobalAccount);
$("#cancel-real-start").addEventListener("click", () => closeRealConfirmation(null));

$("#strategy-select").addEventListener("change", (e) => {
  $("#donchian-fixed-profile").hidden = e.target.value !== "donchian";
  $("#nexus-speed-fixed-profile").hidden = e.target.value !== "nexus_speed";
});
$("#confirm-real-start").addEventListener("click", () => {
  const account = activeAccount(); const phrase = $("#real-confirm-phrase").value.trim(); const expected = `REAL ${account?.account_id || ""}`;
  if (phrase !== expected) { $("#real-confirm-error").textContent = `Digite exatamente: ${expected}`; $("#real-confirm-error").hidden = false; return; }
  closeRealConfirmation(phrase);
});
$("#config-form").addEventListener("submit", async (event) => { event.preventDefault(); const id = $("#config-id").value; const errorNode = $("#form-error"); try { const saved = id ? await api.updateBot(id, formPayload(event.currentTarget)) : await api.createBot(formPayload(event.currentTarget)); closeDrawer(); showChartState("Trocando mercado", `Aplicando ${saved.symbol} · ${timeframe(saved.timeframe_seconds)}.`); await load(saved.id); toast("Configuração salva com sucesso."); } catch (error) { errorNode.textContent = error.message; errorNode.hidden = false; } });
$("#toggle-bot").addEventListener("click", async () => { let bot = selectedBot(); if (!bot) return; const starting = bot.desired_state !== "RUNNING"; const account = activeAccount(); if (starting && !account) { toast("Selecione uma conta global Deriv.", "error"); return; } try { if (starting) { const snapshot = store.get().snapshot || {}; snapshot.active_trade = null; store.set({ snapshot }); renderActiveTrade(null); } if (starting && (bot.account_id !== account.account_id || bot.account_type !== account.account_type)) { const updatedConfig = await api.updateBot(bot.id, configuredBotPayload(bot, account)); Object.assign(bot, updatedConfig); } let realTicket = ""; if (starting && account.account_type === "real") { const phrase = await confirmRealStart(bot, account); if (!phrase) return; realTicket = (await api.realConfirmation(bot.id, phrase)).ticket; } const updated = starting ? await api.startBot(bot.id, realTicket) : await api.stopBot(bot.id); Object.assign(bot, updated); renderBots(); renderHeader(); toast(updated.desired_state === "RUNNING" ? `${account?.account_type === "real" ? "Conta REAL: " : ""}comando de início enviado.` : "Parada segura solicitada."); } catch (error) { handleError(error); } });
$("#stop-all").addEventListener("click", async () => { try { const result = await api.stopAll(); await load(); toast(`${result.stopped} robô(s) receberam parada segura.`); } catch (error) { handleError(error); } });
$("#auth-form").addEventListener("submit", async (event) => { event.preventDefault(); setApiKey($("#api-key").value); $("#auth-error").textContent = ""; $("#auth-gate").hidden = true; await load(); });

function handleError(error) { if (error instanceof ApiError && error.status === 401) { $("#auth-gate").hidden = false; $("#auth-error").textContent = "Chave obrigatória ou inválida."; } else toast(error.message || "Erro inesperado", "error"); }
function timeframe(seconds) { return Number(seconds) <= 1 ? "1s" : Number(seconds) === 300 ? "5m" : "1m"; }
function statusLabel(status) { return ({ RUNNING: "LIVE", STARTING: "START", STOPPING: "STOP", ERROR: "ERRO", STOPPED: "OFF" })[status] || "OFF"; }
function formatTime(value) { if (!value) return "—"; const date = typeof value === "number" ? new Date(value * 1000) : new Date(value); return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }

load();

# NexusTrader Multi-Bot Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. This project must be executed inline in the current session; the user explicitly prohibited subagents.

**Goal:** Deliver a demo-only, multi-bot-ready NexusTrader vertical slice with reliable Deriv reconnection, true orchestration controls, live candles/lines, active-trade annotations, history, and a rebuilt algorithmic trading frontend.

**Architecture:** FastAPI remains the control plane and web server; a separate orchestrator process owns bot sessions and Deriv trading. Persistent desired/runtime state in SQLite coordinates both processes, while a bounded authenticated internal event publisher feeds bot-scoped live WebSockets and chart buffers. The frontend is split into build-free ES modules and uses Lightweight Charts 5.x.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, aiosqlite, httpx, websockets, pandas, unittest, Docker Compose, HTML/CSS/ES modules, Lightweight Charts 5.x.

## Global Constraints

- Runtime integration must use only the current Deriv REST + account OTP + returned WebSocket URL flow.
- Automated execution is limited to accounts whose `account_type` is exactly `demo`.
- No subagents; all work runs inline in this session.
- New behavior follows test-first red-green-refactor cycles.
- The existing Deriv PAT remains in place, but must never be exposed to the browser or new logs.
- One focal chart is visible at a time; line for tick/1s, candles for 60s/300s.
- Existing singular API routes remain as temporary compatibility adapters.

---

### Task 1: Test Harness and Configuration Contracts

**Files:**
- Modify: `config/settings.py`
- Create: `tests/test_settings.py`
- Create: `tests/helpers.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `Settings.is_production`, `Settings.internal_api_token`, `Settings.dashboard_api_key`, `Settings.business_timezone`.

- [ ] **Step 1: Write failing settings tests**

```python
class SettingsContractTests(unittest.TestCase):
    def test_internal_token_must_not_be_empty_in_production(self):
        with self.assertRaises(ValueError):
            Settings(DERIV_APP_ID="app", DERIV_API_TOKEN="pat", DOMAIN="trade.example.com", INTERNAL_API_TOKEN="")

    def test_demo_execution_is_default(self):
        settings = Settings(DERIV_APP_ID="app", DERIV_API_TOKEN="pat")
        self.assertFalse(settings.ALLOW_REAL_TRADING)
```

- [ ] **Step 2: Run the tests and verify the expected failures**

Run: `python -m unittest tests.test_settings -v`
Expected: failure because the new settings and validation do not exist.

- [ ] **Step 3: Add explicit environment contracts and safe defaults**

Add `INTERNAL_API_TOKEN`, `DASHBOARD_API_KEY`, `BUSINESS_TIMEZONE="America/Sao_Paulo"`, `ALLOW_REAL_TRADING=False`, `API_BASE_URL`, queue limits, heartbeat intervals, and production validation.

- [ ] **Step 4: Run settings tests**

Run: `python -m unittest tests.test_settings -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add config/settings.py tests/test_settings.py tests/helpers.py requirements.txt
git commit -m "test: establish runtime configuration contracts"
```

### Task 2: Persistent Multi-Bot Model and Idempotent Trades

**Files:**
- Modify: `database/models.py`
- Modify: `database/repository.py`
- Create: `tests/test_repository.py`

**Interfaces:**
- Produces: `BotInstance`, `DatabaseRepository.list_bots()`, `get_bot(bot_id)`, `create_bot(data)`, `update_bot(bot_id, data)`, `set_desired_state(bot_id, state)`, `set_runtime_state(bot_id, state, error=None)`, `upsert_trade(trade)`.

- [ ] **Step 1: Write failing repository tests against a temporary SQLite file**

```python
async def test_two_bots_keep_independent_state(self):
    first = await self.repo.create_bot(self.bot_payload(name="Bollinger A"))
    second = await self.repo.create_bot(self.bot_payload(name="Bollinger B"))
    await self.repo.set_desired_state(first["id"], "RUNNING")
    self.assertEqual((await self.repo.get_bot(first["id"]))["desired_state"], "RUNNING")
    self.assertEqual((await self.repo.get_bot(second["id"]))["desired_state"], "STOPPED")

async def test_trade_contract_id_is_idempotent(self):
    await self.repo.upsert_trade(self.trade_payload(contract_id=42, profit=0))
    await self.repo.upsert_trade(self.trade_payload(contract_id=42, profit=1.25))
    self.assertEqual(len(await self.repo.list_trades(bot_id="bot-a")), 1)
```

- [ ] **Step 2: Verify repository tests fail for missing APIs**

Run: `python -m unittest tests.test_repository -v`

- [ ] **Step 3: Add additive SQLite migrations**

Create `bot_instances`, add `bot_id`, entry/exit fields and lifecycle state to trades, add a unique index on non-null `contract_id`, and migrate the latest legacy settings into one default demo bot.

- [ ] **Step 4: Implement typed serialization for JSON strategy, money-management, and risk configuration**

Use JSON columns stored as text and return decoded dictionaries at repository boundaries.

- [ ] **Step 5: Run repository tests and legacy API tests**

Run: `python -m unittest tests.test_repository tests.test_api -v`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add database/models.py database/repository.py tests/test_repository.py
git commit -m "feat: add persistent multi-bot runtime model"
```

### Task 3: Reliable Account-Scoped Deriv Connection

**Files:**
- Modify: `core/auth.py`
- Rewrite: `core/connection.py`
- Create: `tests/test_connection.py`

**Interfaces:**
- Consumes: `AuthManager.get_websocket_url(account_id)`.
- Produces: `NexusConnection.connect(account_id)`, `subscribe(key, request, handler)`, `unsubscribe(key)`, `send(request)`, `disconnect()`.

- [ ] **Step 1: Write failing async tests with a fake transport**

```python
async def test_connect_requests_one_account_scoped_otp(self):
    await connection.connect("DOT-DEMO")
    self.assertEqual(auth.requested_accounts, ["DOT-DEMO"])

async def test_reconnect_replays_registered_subscription(self):
    await connection.connect("DOT-DEMO")
    await connection.subscribe("ticks:R_100", {"ticks": "R_100"}, handler)
    await transport.force_close()
    await connection.wait_until_connected(timeout=1)
    self.assertEqual(transport.sent_requests.count({"ticks": "R_100", "subscribe": 1}), 2)
```

- [ ] **Step 2: Verify tests fail because connect has no account argument and subscriptions are not restored**

Run: `python -m unittest tests.test_connection -v`

- [ ] **Step 3: Implement transport injection, single reconnect supervisor, and subscription registry**

Pending futures receive `ConnectionError` on close. Exponential backoff is capped at 30 seconds. Explicit disconnect disables reconnect. Application heartbeat sends `{"ping": 1}`.

- [ ] **Step 4: Add tests for concurrent reconnect coalescing and pending request cleanup**

Run: `python -m unittest tests.test_connection -v`

- [ ] **Step 5: Commit**

```bash
git add core/auth.py core/connection.py tests/test_connection.py
git commit -m "fix: restore Deriv subscriptions after reconnect"
```

### Task 4: Market History, OHLC Aggregation, and Bounded Event Publishing

**Files:**
- Rewrite: `data/market_data.py`
- Create: `data/candles.py`
- Create: `core/events.py`
- Create: `core/event_publisher.py`
- Create: `tests/test_candles.py`
- Create: `tests/test_event_publisher.py`

**Interfaces:**
- Produces: `CandleAggregator.update(epoch, price) -> Candle`, `RuntimeEvent`, `HttpEventPublisher.start()`, `publish(event)`, `close()`, `MarketDataHandler.start(symbol, timeframe)`.

- [ ] **Step 1: Write failing candle tests**

```python
def test_ticks_update_same_minute_candle(self):
    agg = CandleAggregator(60)
    agg.update(120, 10)
    candle = agg.update(150, 13)
    self.assertEqual(candle.as_dict(), {"time": 120, "open": 10, "high": 13, "low": 10, "close": 13})
```

- [ ] **Step 2: Verify candle tests fail, then implement the minimal aggregator**

Run: `python -m unittest tests.test_candles -v`

- [ ] **Step 3: Write failing publisher tests for queue bounds and persistent client reuse**

The queue drops the oldest market tick when full but never drops trade-close, runtime-error, or stop-state events.

- [ ] **Step 4: Implement the publisher worker and market history request**

Use one `httpx.AsyncClient`, `X-Internal-Token`, and a bounded priority-aware queue. Publish `market.history` once, followed by `market.tick` updates.

- [ ] **Step 5: Run market and publisher tests**

Run: `python -m unittest tests.test_candles tests.test_event_publisher -v`

- [ ] **Step 6: Commit**

```bash
git add data/market_data.py data/candles.py core/events.py core/event_publisher.py tests/test_candles.py tests/test_event_publisher.py
git commit -m "feat: stream bounded market history and live events"
```

### Task 5: Demo Guard, Risk Corrections, and Trade Lifecycle

**Files:**
- Modify: `risk/circuit_breaker.py`
- Modify: `risk/risk_manager.py`
- Modify: `trading/monitor.py`
- Modify: `trading/executor.py`
- Modify: `trading/proposal.py`
- Modify: `strategies/base.py`
- Modify: `tests/test_risk.py`
- Create: `tests/test_trade_lifecycle.py`

**Interfaces:**
- Produces: `ensure_demo_account(bot)`, lifecycle event callbacks, idempotent settlement payloads.

- [ ] **Step 1: Keep the existing circuit-breaker regression red**

Run: `python -m unittest tests.test_risk -v`
Expected: three-loss threshold test fails.

- [ ] **Step 2: Change the threshold to `>=` and verify green**

Run: `python -m unittest tests.test_risk -v`

- [ ] **Step 3: Write failing demo-guard and duplicate-settlement tests**

```python
def test_real_account_cannot_execute(self):
    with self.assertRaises(RealTradingDisabled):
        ensure_demo_account({"account_type": "real"})
```

- [ ] **Step 4: Implement guard checks before session start and immediately before buy**

- [ ] **Step 5: Emit `trade.opened`, incremental `trade.updated`, and one `trade.closed` event**

Capture entry spot, current spot, exit spot, purchase/expiry times, status, payout, and profit using the current API field names.

- [ ] **Step 6: Run lifecycle and risk tests**

Run: `python -m unittest tests.test_risk tests.test_trade_lifecycle -v`

- [ ] **Step 7: Commit**

```bash
git add risk trading strategies tests/test_risk.py tests/test_trade_lifecycle.py
git commit -m "fix: enforce demo trading and idempotent risk lifecycle"
```

### Task 6: Multi-Bot Orchestrator and True Start/Stop

**Files:**
- Create: `core/orchestrator.py`
- Create: `core/bot_session.py`
- Rewrite: `main.py`
- Create: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: repository bot desired state and `BotSessionFactory`.
- Produces: `BotOrchestrator.reconcile_once()`, `run()`, `stop()`.

- [ ] **Step 1: Write failing orchestration tests**

```python
async def test_running_desired_state_starts_exactly_one_session(self):
    repo.bots = [bot(desired_state="RUNNING")]
    await orchestrator.reconcile_once()
    await orchestrator.reconcile_once()
    self.assertEqual(factory.started_ids, ["bot-a"])

async def test_stop_prevents_new_entries_but_keeps_open_contract_monitor(self):
    session = factory.session_with_open_contract("bot-a")
    await session.request_stop()
    self.assertFalse(session.accepts_new_entries)
    self.assertTrue(session.monitors_open_contracts)
```

- [ ] **Step 2: Verify tests fail for missing orchestrator**

Run: `python -m unittest tests.test_orchestrator -v`

- [ ] **Step 3: Extract the existing session flow into `BotSession`**

The session receives dependencies, publishes runtime states, detects configuration revision changes, and shuts down cleanly.

- [ ] **Step 4: Implement reconciliation for multiple bot IDs**

Use one task per running bot, explicit cancellation boundaries, and heartbeat updates.

- [ ] **Step 5: Run orchestrator and existing strategy tests**

Run: `python -m unittest tests.test_orchestrator tests.test_bollinger -v`

- [ ] **Step 6: Commit**

```bash
git add core/orchestrator.py core/bot_session.py main.py tests/test_orchestrator.py
git commit -m "feat: orchestrate multiple bot sessions"
```

### Task 7: Control Plane, Live Store, Authentication, and WebSocket Scoping

**Files:**
- Create: `api/auth.py`
- Create: `api/live_store.py`
- Create: `api/routes/bots.py`
- Create: `api/routes/internal.py`
- Modify: `api/app.py`
- Rewrite: `api/websocket_manager.py`
- Modify: `api/routes/bot_control.py`
- Modify: `api/routes/trades.py`
- Create: `tests/test_control_plane.py`

**Interfaces:**
- Produces: primary bot CRUD/command routes, authenticated internal event endpoint, bot-scoped snapshots and broadcasts.

- [ ] **Step 1: Write failing API security and command tests**

```python
def test_internal_event_rejects_missing_token(self):
    self.assertEqual(self.client.post("/api/v1/internal/events", json=self.event()).status_code, 401)

def test_stop_changes_persistent_desired_state(self):
    response = self.client.post(f"/api/v1/bots/{self.bot_id}/stop", headers=self.dashboard_headers)
    self.assertEqual(response.json()["data"]["desired_state"], "STOPPED")
```

- [ ] **Step 2: Verify failures, then implement authentication dependencies and routes**

Protect account, config, trade, command, docs, and OpenAPI surfaces when `DASHBOARD_API_KEY` is configured. Protect internal events unconditionally.

- [ ] **Step 3: Implement live ring buffers and bot-scoped WebSockets**

On connect, send `snapshot` before incremental events. Browser connection state must not overwrite Deriv runtime state.

- [ ] **Step 4: Keep singular endpoints as default-bot adapters**

- [ ] **Step 5: Run API tests**

Run: `python -m unittest tests.test_api tests.test_control_plane -v`

- [ ] **Step 6: Commit**

```bash
git add api tests/test_control_plane.py
git commit -m "feat: expose authenticated multi-bot control plane"
```

### Task 8: Rebuild the Algorithmic Trading Frontend

**Files:**
- Rewrite: `static/index.html`
- Create: `static/styles.css`
- Create: `static/js/api.js`
- Create: `static/js/store.js`
- Create: `static/js/chart.js`
- Create: `static/js/app.js`
- Create: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: bot APIs, market history, bot-scoped live WebSocket events.
- Produces: operational day-trading workspace and configuration sheet.

- [ ] **Step 1: Write failing static contract tests**

Assert that the shell contains the bot rail, chart viewport, operation rail, history dock, configuration sheet, live-status elements, and module entrypoint; assert that the old Chart.js CDN is absent.

- [ ] **Step 2: Verify the tests fail against the provisional dashboard**

Run: `python -m unittest tests.test_frontend_contract -v`

- [ ] **Step 3: Build semantic HTML and responsive CSS shell**

Implement desktop tri-pane, mobile portrait drawers, mobile landscape chart mode, accessible labels, keyboard focus, and reduced-motion styles.

- [ ] **Step 4: Implement REST/store/application modules**

Support robot selection, API-key session entry, configuration, start, stop, reconnect with backoff, snapshot restoration, event tape, trade history, and stale detection.

- [ ] **Step 5: Implement Lightweight Charts 5.x module**

Use `LineSeries` for tick/1s, `CandlestickSeries` for 60s/300s, incremental `update`, Bollinger line series, entry/exit markers, active price line, resize observer, and return-to-realtime.

- [ ] **Step 6: Run static contract and API tests**

Run: `python -m unittest tests.test_frontend_contract tests.test_control_plane -v`

- [ ] **Step 7: Commit**

```bash
git add static tests/test_frontend_contract.py
git commit -m "feat: rebuild algorithmic trading workspace"
```

### Task 9: Docker, Health Checks, and Complete Documentation

**Files:**
- Modify: `docker-compose.yml`
- Modify: `Dockerfile`
- Modify: `deploy/deploy.sh`
- Rewrite: `deploy/DEPLOY.md`
- Create: `.env.example`
- Create: `docs/ARCHITECTURE.md`
- Create: `docs/API.md`
- Create: `docs/DEVELOPMENT.md`
- Create: `docs/OPERATIONS.md`
- Rewrite: `docs/README.md`
- Update: `docs/ROADMAP.md`

**Interfaces:**
- Produces: reproducible deployment, service health checks, operator runbook, and full developer handoff.

- [ ] **Step 1: Add compose config verification expectations**

Both services receive the same `INTERNAL_API_TOKEN`, the bot receives `API_BASE_URL=http://nexus-api:8000`, and health-gated startup replaces start-order-only `depends_on`.

- [ ] **Step 2: Add `/api/v1/health/live` and `/api/v1/health/ready`**

Readiness checks database initialization; runtime state remains separate and bot-specific.

- [ ] **Step 3: Write the documentation set from the validated contracts**

Include local setup, test commands, current Deriv flow, API examples, event schemas, deployment variables, backup/restore, log patterns, incident recovery, demo smoke test, and roadmap.

- [ ] **Step 4: Validate compose and documentation links**

Run: `docker compose config`
Run: `python -m unittest discover -s tests -v`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml Dockerfile deploy .env.example docs api/app.py
git commit -m "docs: complete deployment and operations handoff"
```

### Task 10: Full Verification and Browser QA

**Files:**
- Modify only files required by defects found during verification.

**Interfaces:**
- Verifies the complete vertical slice.

- [ ] **Step 1: Run all automated tests**

Run: `python -m unittest discover -s tests -v`
Expected: zero failures and zero errors.

- [ ] **Step 2: Run syntax, whitespace, and compose verification**

Run: `python -m compileall -q .`
Run: `git diff --check`
Run: `docker compose config`

- [ ] **Step 3: Start the local stack and run API smoke checks**

Run: `docker compose up -d --build`
Verify health, list bots, start default demo bot, receive snapshot and live events, stop it, and confirm desired/runtime states converge.

- [ ] **Step 4: Verify the desktop and mobile frontend in a browser**

Check line and candle modes, live/stale state, chart resizing, bot selection, configuration sheet, start/stop, active operation annotations, history dock, mobile portrait, and mobile landscape.

- [ ] **Step 5: Confirm production deployment remains a separate authorized action**

Document the exact VPS deploy and smoke-test commands without executing them unless server access and deployment authority are available.

- [ ] **Step 6: Commit verification fixes**

```bash
git add -A
git commit -m "test: verify multi-bot demo trading vertical slice"
```

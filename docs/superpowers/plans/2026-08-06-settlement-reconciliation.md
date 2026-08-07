# Settlement Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile expired Deriv contracts until `is_sold=1`, publish exactly one terminal event, and show an accurate awaiting-settlement state in the dashboard.

**Architecture:** `ContractMonitor` remains the single owner of contract monitoring and merges subscription updates with post-expiry point queries through one idempotent handler. `BotSession` publishes explicit lifecycle telemetry and owns persistence/risk ordering, while a pure JavaScript presenter maps live, awaiting, and closed trades to UI text.

**Tech Stack:** Python 3.11, asyncio, FastAPI, SQLite/aiosqlite, unittest, browser ES modules, Node test runner.

## Global Constraints

- Only `is_sold=1` may close a contract or produce `trade.closed`.
- `status` remains `open` or `closed`; `lifecycle_state` is telemetry with exact values `live`, `awaiting_settlement`, and `closed`.
- Defaults are `CONTRACT_RECONCILE_INTERVAL_SECONDS=5` and `CONTRACT_EXPIRY_GRACE_SECONDS=1`.
- Reconciliation retries point queries but never retries `buy`.
- Settlement persistence must finish before the contract is marked idempotently processed.
- Donchian+ZigZag stays fixed at `donchian`, `R_75`, 60-second candles, and two-minute contracts.
- Do not modify `strategies/donchian_zigzag.py` or `utils/indicators.py`.
- Validation is DEMO-only; never place or authorize a REAL order.
- Do not print or persist PATs, OTP URLs, dashboard keys, internal tokens, or notification tokens.

---

## File map

- `config/settings.py`: reconciliation interval and expiry grace validation.
- `trading/monitor.py`: subscription/query convergence, task ownership, retry, idempotency, and cleanup.
- `core/bot_session.py`: monitor lifecycle and lifecycle-aware trade payloads.
- `static/js/trade_state.js`: pure UI-state derivation.
- `static/js/app.js`: render live versus awaiting-settlement contracts.
- `static/styles.css`: visual state for a contract awaiting Deriv settlement.
- `tests/test_settings.py`: invalid reconciliation setting behavior.
- `tests/test_trade_lifecycle.py`: monitor and session regression coverage.
- `tests/js/trade_state.test.mjs`: deterministic browser presentation tests.
- `docs/CURRENT-STATE.md`, `docs/AUDIT-2026-08-06.md`, `docs/API.md`: implemented behavior and evidence.

### Task 1: Reconciliation settings contract

**Files:**
- Modify: `config/settings.py`
- Modify: `tests/test_settings.py`

**Interfaces:**
- Produces: `settings.CONTRACT_RECONCILE_INTERVAL_SECONDS: int` and `settings.CONTRACT_EXPIRY_GRACE_SECONDS: int`.
- Consumed by: Task 2 `ContractMonitor` defaults.

- [ ] **Step 1: Write failing setting-validation tests**

Add to `SettingsContractTests`:

```python
def test_contract_reconcile_interval_must_be_positive(self):
    with self.assertRaises(ValueError):
        Settings(
            _env_file=None,
            DERIV_APP_ID="test-app",
            DERIV_API_TOKEN="test-token",
            DEV_MODE=True,
            CONTRACT_RECONCILE_INTERVAL_SECONDS=0,
        )

def test_contract_expiry_grace_cannot_be_negative(self):
    with self.assertRaises(ValueError):
        Settings(
            _env_file=None,
            DERIV_APP_ID="test-app",
            DERIV_API_TOKEN="test-token",
            DEV_MODE=True,
            CONTRACT_EXPIRY_GRACE_SECONDS=-1,
        )

def test_contract_reconciliation_defaults_are_safe(self):
    configured = Settings(
        _env_file=None,
        DERIV_APP_ID="test-app",
        DERIV_API_TOKEN="test-token",
        DEV_MODE=True,
    )
    self.assertEqual(configured.CONTRACT_RECONCILE_INTERVAL_SECONDS, 5)
    self.assertEqual(configured.CONTRACT_EXPIRY_GRACE_SECONDS, 1)
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
rtk .\.venv\Scripts\python.exe -m unittest tests.test_settings -v
```

Expected: the invalid values do not raise and the new attributes do not exist.

- [ ] **Step 3: Implement the validated settings**

Add fields beside the existing settlement settings:

```python
CONTRACT_RECONCILE_INTERVAL_SECONDS: int = 5
CONTRACT_EXPIRY_GRACE_SECONDS: int = 1
```

Add to `validate_production_secrets`:

```python
if self.CONTRACT_RECONCILE_INTERVAL_SECONDS < 1:
    raise ValueError("CONTRACT_RECONCILE_INTERVAL_SECONDS deve ser pelo menos 1")
if self.CONTRACT_EXPIRY_GRACE_SECONDS < 0:
    raise ValueError("CONTRACT_EXPIRY_GRACE_SECONDS nao pode ser negativo")
```

- [ ] **Step 4: Verify GREEN**

Run the Task 1 test command and confirm all settings tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
rtk git add config/settings.py tests/test_settings.py
rtk git commit -m "config: add settlement reconciliation timing"
```

### Task 2: ContractMonitor subscription and point-query convergence

**Files:**
- Modify: `trading/monitor.py`
- Modify: `tests/test_trade_lifecycle.py`

**Interfaces:**
- Consumes: Task 1 settings.
- Produces: `ContractMonitor.close()`, post-expiry reconciliation tasks, and one shared payload handler.
- Preserves: `monitor_contract(contract_id, on_settled_callback, on_update_callback=None)`.

- [ ] **Step 1: Extend the fake connection without weakening existing assertions**

Give `FakeConnection` queued point responses while keeping `send()` behavior for executor tests:

```python
class FakeConnection:
    def __init__(self):
        self.sent = []
        self.subscriptions = {}
        self.unsubscribed = []
        self.contract_responses = []

    async def send(self, request):
        self.sent.append(request)
        if "proposal_open_contract" in request and self.contract_responses:
            return self.contract_responses.pop(0)
        return {"buy": {"contract_id": 42}}
```

- [ ] **Step 2: Write failing reconciliation tests**

Add tests whose production mutation is “remove the fallback point query”:

```python
async def test_point_query_settles_when_subscription_misses_terminal_update(self):
    connection = FakeConnection()
    connection.contract_responses.append({
        "proposal_open_contract": {
            "contract_id": 42, "is_sold": 1, "is_expired": 1,
            "status": "lost", "profit": "-1.00",
        }
    })
    settled = []
    monitor = ContractMonitor(connection, reconcile_interval_seconds=0.01, expiry_grace_seconds=0)
    await monitor.monitor_contract(42, lambda contract: settled.append(contract) or _done())
    callback = connection.subscriptions["contract:42"]
    await callback({"proposal_open_contract": {
        "contract_id": 42, "is_sold": 0, "is_expired": 1,
        "date_expiry": 1, "status": "open", "profit": "-1.00",
    }})
    await asyncio.wait_for(_wait_until(lambda: len(settled) == 1), timeout=0.5)
    self.assertEqual(settled[0]["status"], "lost")
    self.assertEqual(connection.unsubscribed, ["contract:42"])
    await monitor.close()
```

Define test-only async helpers above the test classes:

```python
async def _done():
    return None

async def _wait_until(predicate):
    while not predicate():
        await asyncio.sleep(0)
```

Also add these focused behaviors. For the first, queue one expired-unsold response and
one sold response, then assert that two point queries occur and only the second response
settles:

```python
async def test_expired_unsold_query_keeps_reconciling_until_sold(self):
    connection = FakeConnection()
    connection.contract_responses.extend([
        {"proposal_open_contract": {
            "contract_id": 42, "contract_type": "CALL", "currency": "USD",
            "is_sold": 0, "is_expired": 1, "date_expiry": 1,
            "status": "open", "profit": "-1.00", "payout": "0",
        }},
        {"proposal_open_contract": {
            "contract_id": 42, "contract_type": "CALL", "currency": "USD",
            "is_sold": 1, "is_expired": 1, "date_expiry": 1,
            "status": "lost", "profit": "-1.00", "payout": "0",
        }},
    ])
    settled = []
    monitor = ContractMonitor(connection, reconcile_interval_seconds=0.01, expiry_grace_seconds=0)
    await monitor.monitor_contract(42, lambda contract: settled.append(contract) or _done())
    await connection.subscriptions["contract:42"]({"proposal_open_contract": {
        "contract_id": 42, "contract_type": "CALL", "currency": "USD",
        "is_sold": 0, "is_expired": 1, "date_expiry": 1,
        "status": "open", "profit": "-1.00", "payout": "0",
    }})
    await asyncio.wait_for(_wait_until(lambda: len(settled) == 1), timeout=0.5)
    queries = [item for item in connection.sent if "proposal_open_contract" in item]
    self.assertEqual(len(queries), 2)
    await monitor.close()
```

For the race, define a local `BlockingConnection` whose point-query `send()` sets a
`query_started` event, waits for `release_query`, and then returns the sold payload.
Start reconciliation with an expired-open subscription payload, wait for
`query_started`, deliver the same sold payload through the subscription, release the
query, and assert the settlement list is `[42]` and unsubscribe is called once:

```python
async def test_subscription_and_query_terminal_updates_settle_once(self):
    sold = {"proposal_open_contract": {
        "contract_id": 42, "contract_type": "CALL", "currency": "USD",
        "is_sold": 1, "is_expired": 1, "date_expiry": 1,
        "status": "won", "profit": "0.95", "payout": "1.95",
    }}

    class BlockingConnection(FakeConnection):
        def __init__(self):
            super().__init__()
            self.query_started = asyncio.Event()
            self.release_query = asyncio.Event()

        async def send(self, request):
            self.sent.append(request)
            self.query_started.set()
            await self.release_query.wait()
            return sold

    connection = BlockingConnection()
    settlements = []
    monitor = ContractMonitor(connection, reconcile_interval_seconds=0.01, expiry_grace_seconds=0)
    await monitor.monitor_contract(42, lambda contract: settlements.append(contract["contract_id"]) or _done())
    callback = connection.subscriptions["contract:42"]
    await callback({"proposal_open_contract": {
        "contract_id": 42, "contract_type": "CALL", "currency": "USD",
        "is_sold": 0, "is_expired": 1, "date_expiry": 1,
        "status": "open", "profit": "0", "payout": "1.95",
    }})
    await asyncio.wait_for(connection.query_started.wait(), timeout=0.5)
    await callback(sold)
    connection.release_query.set()
    await asyncio.sleep(0)
    self.assertEqual(settlements, [42])
    self.assertEqual(connection.unsubscribed, ["contract:42"])
    await monitor.close()
```

For missing expiry, start the monitor without invoking its subscription callback, queue
a sold point response, wait for settlement, and assert the exact point request was sent:

```python
async def test_missing_expiry_starts_fallback_after_interval(self):
    connection = FakeConnection()
    connection.contract_responses.append({"proposal_open_contract": {
        "contract_id": 42, "contract_type": "CALL", "currency": "USD",
        "is_sold": 1, "is_expired": 1, "date_expiry": 1,
        "status": "lost", "profit": "-1.00", "payout": "0",
    }})
    settlements = []
    monitor = ContractMonitor(connection, reconcile_interval_seconds=0.01, expiry_grace_seconds=0)
    await monitor.monitor_contract(42, lambda contract: settlements.append(contract["contract_id"]) or _done())
    await asyncio.wait_for(_wait_until(lambda: settlements == [42]), timeout=0.5)
    self.assertIn({"proposal_open_contract": 1, "contract_id": 42}, connection.sent)
    await monitor.close()
```

For cleanup, use a blocking point query, wait until it starts, call `close()`, and verify
that the method returns promptly and unsubscribes without invoking settlement:

```python
async def test_close_cancels_reconciliation_without_settlement(self):
    class BlockingConnection(FakeConnection):
        def __init__(self):
            super().__init__()
            self.query_started = asyncio.Event()
            self.never = asyncio.Event()

        async def send(self, request):
            self.sent.append(request)
            self.query_started.set()
            await self.never.wait()

    connection = BlockingConnection()
    settlements = []
    monitor = ContractMonitor(connection, reconcile_interval_seconds=0.01, expiry_grace_seconds=0)
    await monitor.monitor_contract(42, lambda contract: settlements.append(contract) or _done())
    await asyncio.wait_for(connection.query_started.wait(), timeout=0.5)
    await asyncio.wait_for(monitor.close(), timeout=0.5)
    self.assertEqual(settlements, [])
    self.assertEqual(connection.unsubscribed, ["contract:42"])
```

Each fixture must use full Deriv-shaped payloads with `contract_id`, `contract_type`,
`currency`, `is_sold`, `is_expired`, `date_expiry`, `status`, `profit`, and `payout`.
Assert observable settlements, forwarded open updates, sent point-query payloads, and
unsubscribe effects; do not assert private task containers.

- [ ] **Step 3: Verify RED**

Run:

```powershell
rtk .\.venv\Scripts\python.exe -m unittest tests.test_trade_lifecycle.ContractSettlementTests -v
```

Expected: constructor arguments and `close()` are unsupported, and no point query settles the contract.

- [ ] **Step 4: Implement one shared contract-update handler**

Refactor `ContractMonitor` around this shape:

```python
class ContractMonitor:
    def __init__(self, connection, reconcile_interval_seconds=None, expiry_grace_seconds=None):
        self.connection = connection
        self.reconcile_interval_seconds = float(
            settings.CONTRACT_RECONCILE_INTERVAL_SECONDS
            if reconcile_interval_seconds is None else reconcile_interval_seconds
        )
        self.expiry_grace_seconds = float(
            settings.CONTRACT_EXPIRY_GRACE_SECONDS
            if expiry_grace_seconds is None else expiry_grace_seconds
        )
        self._settled_contracts = set()
        self._settlement_locks = {}
        self._reconciliation_tasks = {}
        self._expiry_events = {}
        self._expiry_times = {}
        self._closed = False
```

`monitor_contract` registers the existing subscription, creates an expiry event, and
starts exactly one reconciliation task. Both subscription and point-query paths call:

```python
async def _handle_contract_payload(
    self, contract_id, data, on_settled_callback, on_update_callback
):
    poc = data.get("proposal_open_contract")
    if not poc or int(poc.get("contract_id", -1)) != int(contract_id):
        return
    if poc.get("date_expiry") is not None:
        self._expiry_times[contract_id] = float(poc["date_expiry"])
        self._expiry_events[contract_id].set()
    if poc.get("is_sold") != 1:
        if on_update_callback:
            await on_update_callback(poc)
        return
    lock = self._settlement_locks.setdefault(contract_id, asyncio.Lock())
    async with lock:
        if contract_id in self._settled_contracts:
            return
        await on_settled_callback(poc)
        self._settled_contracts.add(contract_id)
        await self.connection.unsubscribe(f"contract:{contract_id}")
        current = asyncio.current_task()
        task = self._reconciliation_tasks.pop(contract_id, None)
        if task and task is not current:
            task.cancel()
```

The reconciliation coroutine waits for the known expiry plus grace. When expiry is not
known, it waits one interval and performs a safety query. It sends exactly:

```python
{"proposal_open_contract": 1, "contract_id": contract_id}
```

It passes successful responses to `_handle_contract_payload`, sleeps the configured
interval while unresolved, tolerates error responses, and exits on close, cancellation,
or membership in `_settled_contracts`.

`close()` sets `_closed`, cancels and awaits all reconciliation tasks, unsubscribes all
still-monitored contract keys, and clears its task/event maps idempotently.

- [ ] **Step 5: Verify GREEN and preserve old behavior**

Run:

```powershell
rtk .\.venv\Scripts\python.exe -m unittest tests.test_trade_lifecycle -v
```

Confirm all old settlement/recovery tests and the new fallback tests pass without pending-task warnings.

- [ ] **Step 6: Commit Task 2**

```powershell
rtk git add trading/monitor.py tests/test_trade_lifecycle.py
rtk git commit -m "fix: reconcile terminal contract status"
```

### Task 3: BotSession lifecycle telemetry and monitor cleanup

**Files:**
- Modify: `core/bot_session.py`
- Modify: `tests/test_trade_lifecycle.py`

**Interfaces:**
- Consumes: Task 2 `ContractMonitor.close()`.
- Produces: trade payload field `lifecycle_state` and deterministic monitor cleanup before socket disconnect.

- [ ] **Step 1: Write failing payload and cleanup tests**

Add a payload test using a real `BotSession` and a strategy stub:

```python
def test_expired_unsold_payload_is_awaiting_settlement(self):
    session = BotSession(object(), {"id": "bot-a", "symbol": "R_75"}, publisher=object())
    strategy = type("Strategy", (), {"name": lambda self: "Donchian+ZigZag"})()
    payload = session._trade_payload({
        "contract_id": 42, "contract_type": "CALL", "underlying": "R_75",
        "is_sold": 0, "is_expired": 1, "status": "open", "profit": "-1.00",
        "buy_price": "1.00", "payout": "0", "date_expiry": 100,
    }, strategy, "open")
    self.assertEqual(payload["status"], "open")
    self.assertEqual(payload["lifecycle_state"], "awaiting_settlement")
```

Add live and closed cases. Add a run-cleanup test with fakes that makes the session exit
after initialization and asserts monitor `close()` happens before connection
`disconnect()`. Patch `core.bot_session.ContractMonitor` and the existing external
dependencies at their narrow boundaries; assert the ordered side-effect list from the
real `BotSession.run()` finally block.

```python
async def test_run_closes_monitor_before_connection(self):
    events = []

    class Repository:
        async def create_session(self, session_id): return None
        async def set_runtime_state(self, bot_id, status, error=None): return None
        async def close_session(self, session_id, status="closed"): return None
        async def list_trades(self, bot_id, limit=1000): return []

    class Publisher:
        async def start(self): return None
        async def publish(self, event): return True

    class Auth:
        async def list_accounts(self):
            return [{"account_id": "DOT-DEMO", "account_type": "demo", "status": "active"}]
        async def close(self): events.append("auth")

    class Connection:
        def __init__(self, auth): self.auth = auth
        async def connect(self, account_id): return True
        async def disconnect(self): events.append("connection")

    class Market:
        def __init__(self, *args, **kwargs): pass
        async def start(self, symbol, timeframe): return None
        async def close(self): events.append("market")

    class Monitor:
        def __init__(self, connection): pass
        async def close(self): events.append("monitor")

    class CompletedSession(BotSession):
        async def _trade_loop(self, *args): return None

    bot = {
        "id": "bot-a", "account_id": "DOT-DEMO", "account_type": "demo",
        "strategy_id": "donchian", "symbol": "R_75", "timeframe_seconds": 60,
        "duration": 2, "duration_unit": "m", "initial_stake": 1.0,
    }
    auth = Auth()
    with patch("core.bot_session.AuthManager", return_value=auth), \
         patch("core.bot_session.NexusConnection", Connection), \
         patch("core.bot_session.MarketDataHandler", Market), \
         patch("core.bot_session.ContractMonitor", Monitor):
        await CompletedSession(Repository(), bot, publisher=Publisher()).run()

    self.assertEqual(events, ["monitor", "market", "connection"])
```

- [ ] **Step 2: Verify RED**

Run the Task 2 test command. Expected: `lifecycle_state` is absent and monitor cleanup is not invoked.

- [ ] **Step 3: Implement lifecycle payloads and cleanup**

Initialize `monitor = None` before the session `try`, assign the monitor after connecting,
and close it first in `finally`:

```python
finally:
    if monitor:
        await monitor.close()
    if self._market_data:
        await self._market_data.close()
    if self._connection:
        await self._connection.disconnect()
```

Derive payload lifecycle without changing persistence status:

```python
is_sold = poc.get("is_sold") == 1
is_expired = poc.get("is_expired") == 1
lifecycle_state = "closed" if status == "closed" or is_sold else (
    "awaiting_settlement" if is_expired else "live"
)
```

Include `lifecycle_state`, `is_sold`, `is_expired`, and `date_settlement` in the returned
payload. Open trade creation uses `lifecycle_state="live"`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
rtk .\.venv\Scripts\python.exe -m unittest tests.test_trade_lifecycle tests.test_orchestrator -v
```

- [ ] **Step 5: Commit Task 3**

```powershell
rtk git add core/bot_session.py tests/test_trade_lifecycle.py
rtk git commit -m "fix: publish contract lifecycle state"
```

### Task 4: Awaiting-settlement dashboard state

**Files:**
- Create: `static/js/trade_state.js`
- Create: `tests/js/trade_state.test.mjs`
- Modify: `static/js/app.js`
- Modify: `static/styles.css`

**Interfaces:**
- Consumes: Task 3 `trade.lifecycle_state`.
- Produces: `contractPresentation(trade, nowEpoch)` and `formatCountdown(expiry, nowEpoch)`.

- [ ] **Step 1: Write failing pure JavaScript tests**

Create `tests/js/trade_state.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { contractPresentation, formatCountdown } from "../../static/js/trade_state.js";

test("live contract retains countdown and floating pnl", () => {
  assert.deepEqual(contractPresentation({ status: "open", expiry_time: 160 }, 100), {
    state: "live", chip: "AO VIVO", pnlLabel: "P&L FLUTUANTE",
    countdown: "1:00", countdownLabel: "Expira em",
  });
});

test("expired open contract waits for Deriv settlement", () => {
  assert.deepEqual(contractPresentation({ status: "open", expiry_time: 100 }, 101), {
    state: "awaiting_settlement", chip: "AGUARDANDO",
    pnlLabel: "RESULTADO PROVISÓRIO", countdown: "Aguardando liquidação",
    countdownLabel: "Liquidação Deriv",
  });
});

test("backend awaiting state wins over a slow local clock", () => {
  assert.equal(contractPresentation({
    status: "open", lifecycle_state: "awaiting_settlement", expiry_time: 200,
  }, 100).state, "awaiting_settlement");
});

test("missing expiry remains live without inventing a deadline", () => {
  assert.deepEqual(contractPresentation({ status: "open", expiry_time: null }, 100), {
    state: "live", chip: "AO VIVO", pnlLabel: "P&L FLUTUANTE",
    countdown: "—", countdownLabel: "Expira em",
  });
});

test("closed contract is not presented as active", () => {
  assert.equal(contractPresentation({ status: "closed", expiry_time: 100 }, 100), null);
});

test("countdown is deterministic", () => {
  assert.equal(formatCountdown(181, 120), "1:01");
});
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
rtk node --test tests/js/trade_state.test.mjs
```

Expected: module not found.

- [ ] **Step 3: Implement the pure presenter**

Create `static/js/trade_state.js`:

```javascript
export function formatCountdown(expiry, nowEpoch) {
  if (expiry === null || expiry === undefined || expiry === "") return "—";
  const remaining = Math.max(0, Number(expiry || 0) - Number(nowEpoch));
  return `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
}

export function contractPresentation(trade, nowEpoch = Math.floor(Date.now() / 1000)) {
  if (!trade || trade.status === "closed" || trade.lifecycle_state === "closed") return null;
  const awaiting = trade.lifecycle_state === "awaiting_settlement"
    || (trade.expiry_time !== null && trade.expiry_time !== undefined
      && trade.expiry_time !== "" && Number(trade.expiry_time) <= Number(nowEpoch));
  if (awaiting) return {
    state: "awaiting_settlement", chip: "AGUARDANDO",
    pnlLabel: "RESULTADO PROVISÓRIO", countdown: "Aguardando liquidação",
    countdownLabel: "Liquidação Deriv",
  };
  return {
    state: "live", chip: "AO VIVO", pnlLabel: "P&L FLUTUANTE",
    countdown: formatCountdown(trade.expiry_time, nowEpoch), countdownLabel: "Expira em",
  };
}
```

- [ ] **Step 4: Integrate the presenter into `app.js`**

Import the module and make `renderActiveTrade`/the one-second timer derive presentation
on every tick. The DOM must expose IDs for the chip, P&L label, countdown label, and
countdown value. Apply CSS classes `live` and `waiting` to the existing status chip; do
not clear the card when presentation is awaiting. `trade.closed` remains the only event
that clears the active trade and adds a journal row.

Use this integration shape:

```javascript
import { contractPresentation } from "./trade_state.js";

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
```

Render `id="trade-pnl-label"` and `id="trade-countdown-label"` in the active-card HTML,
call `updateActiveTradePresentation(trade)` immediately, and call it from the one-second
timer. Remove the old `updateCountdown()` function after all callers move.

Add a visible but non-terminal waiting style:

```css
.status-chip.waiting {
  color: var(--amber);
  border-color: color-mix(in srgb, var(--amber) 45%, transparent);
  background: color-mix(in srgb, var(--amber) 12%, transparent);
}
```

- [ ] **Step 5: Verify GREEN and frontend contract**

Run:

```powershell
rtk node --test tests/js/*.test.mjs
rtk .\.venv\Scripts\python.exe -m unittest tests.test_frontend_contract -v
```

- [ ] **Step 6: Commit Task 4**

```powershell
rtk git add static/js/trade_state.js static/js/app.js static/styles.css tests/js/trade_state.test.mjs
rtk git commit -m "fix: show contracts awaiting settlement"
```

### Task 5: Documentation, regression suite, and DEMO recovery

**Files:**
- Modify: `docs/CURRENT-STATE.md`
- Modify: `docs/AUDIT-2026-08-06.md`
- Modify: `docs/API.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: canonical operational documentation and fresh verification evidence.

- [ ] **Step 1: Document the implemented lifecycle**

Update the canonical documents to state:

- subscriptions are backed by post-expiry `proposal_open_contract` reconciliation;
- `is_sold=1` remains the only terminal gate;
- active snapshots expose `lifecycle_state`;
- the browser displays `AGUARDANDO LIQUIDAÇÃO` without counting provisional P&L;
- the observed stuck DEMO contract was recovered idempotently after rollout.

Do not claim recovery evidence until Step 4 observes it.

- [ ] **Step 2: Run the complete automated baseline**

```powershell
rtk .\.venv\Scripts\python.exe -m unittest discover -s tests -v
rtk .\.venv\Scripts\python.exe -m compileall -q api core data database risk strategies trading
rtk node --test tests/js/*.test.mjs
rtk .\.venv\Scripts\python.exe -m pip check
rtk git diff --check
rtk git diff --exit-code a6581e7 -- strategies/donchian_zigzag.py utils/indicators.py
```

Expected: all commands exit 0; Python and JavaScript test counts are reported explicitly.

- [ ] **Step 3: Restart the localhost stack safely**

Read `storage/dev-api.pid` and `storage/dev-bot.pid`, resolve the exact child processes
belonging to this workspace, and stop only those processes. Then run:

```powershell
rtk powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1 -Detached
```

Confirm `http://127.0.0.1:8990/api/v1/health` is `ok`. The launcher must keep
`ALLOW_REAL_TRADING=false`, `DERIV_ACCOUNT_TYPE=demo`, and the isolated development DB.

- [ ] **Step 4: Verify the observed contract recovery without a new buy**

Using authenticated read-only API calls and a sanitized SQLite query, verify:

- the previously open contract is `closed/lost` with profit `-50.00`;
- the snapshot `active_trade` is null;
- recent trades contains the contract exactly once;
- the browser journal shows one loss and no active card;
- logs show no second `buy` during recovery.

If the bot remains desired `RUNNING`, stop it through the local DEMO control endpoint
before restarting to prevent a fresh signal during validation. This stop is local and
does not alter or close the already-settled Deriv contract.

- [ ] **Step 5: Finalize evidence and commit Task 5**

Only after Step 4, add the observed evidence to `docs/AUDIT-2026-08-06.md`, then run
`git diff --check` again and commit:

```powershell
rtk git add docs/CURRENT-STATE.md docs/AUDIT-2026-08-06.md docs/API.md
rtk git commit -m "docs: record settlement reconciliation evidence"
```

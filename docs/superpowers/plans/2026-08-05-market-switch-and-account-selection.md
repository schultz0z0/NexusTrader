# Market Switch and Account Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make market changes atomic, deduplicate chart trade markers, enable controlled real/demo account selection, and prepare the current VPS deployment.

**Architecture:** A market-context identity controls chart/read-model acceptance. A normalized account catalog is shared by API validation, bot session validation and the frontend selector. Real execution remains environment-gated and requires explicit UI confirmation.

**Tech Stack:** Python 3.11, FastAPI, asyncio, SQLite, vanilla ES modules, Lightweight Charts 5, Node test runner, Docker Compose/Traefik.

## Global Constraints

- Do not send any automated order to a real account.
- Demo remains the default and validation account.
- Use the current Deriv REST accounts/OTP API, never legacy `authorize`.
- Preserve safe stop of active contracts.
- Keep the existing production domain and persistent SQLite volume.

---

### Task 1: Chart context and marker regression tests

**Files:**
- Create: `tests/js/chart.test.mjs`
- Modify: `static/js/chart.js`

**Interfaces:**
- Consumes: `TradingChart.setHistory`, `updateTick`, `showTrade`, `closeTrade`.
- Produces: context-aware series reset and marker upsert keyed by contract.

- [ ] Write Node tests proving stale Bollinger data survives a same-mode symbol switch and repeated updates duplicate entry markers.
- [ ] Run `node --test tests/js/chart.test.mjs` and confirm both tests fail for those behaviors.
- [ ] Implement context identity, complete context reset, same-context indicator clearing and keyed marker upsert.
- [ ] Run the Node tests and confirm they pass.
- [ ] Commit the chart regression fix.

### Task 2: Event-context and switch lifecycle

**Files:**
- Modify: `tests/test_control_plane.py`
- Modify: `api/live_store.py`
- Modify: `static/js/app.js`

**Interfaces:**
- Consumes: normalized `market.history` and `market.tick` events.
- Produces: stale-tick rejection and one post-save selection/subscription.

- [ ] Add a failing LiveStore test showing an old-symbol tick is appended after new history.
- [ ] Confirm the test fails.
- [ ] Reject mismatched symbol/timeframe ticks in LiveStore.
- [ ] Make frontend snapshot/event rendering require the selected bot context and show switching state otherwise.
- [ ] Replace `load(); selectBot(saved.id)` with one `load(saved.id)` flow.
- [ ] Run Python and Node syntax tests.
- [ ] Commit the atomic market-switch behavior.

### Task 3: Normalized Deriv accounts and real gate

**Files:**
- Create: `core/accounts.py`
- Create: `tests/test_accounts.py`
- Modify: `api/app.py`
- Modify: `api/routes/bots.py`
- Modify: `core/bot_session.py`
- Modify: `trading/safety.py`
- Modify: `trading/executor.py`

**Interfaces:**
- Produces: `normalize_account(dict) -> dict`, `validate_selected_account(bot, account) -> dict`, `GET /api/v1/accounts`.

- [ ] Add failing tests for normalization, disabled real, enabled real and type mismatch.
- [ ] Confirm expected failures.
- [ ] Implement normalization and selection validation.
- [ ] Add the authenticated account catalog route.
- [ ] Pass the actual selected account type through BotSession and OrderExecutor.
- [ ] Run targeted and full Python tests.
- [ ] Commit backend account switching.

### Task 4: Account selector and real-mode UX

**Files:**
- Modify: `static/index.html`
- Modify: `static/styles.css`
- Modify: `static/js/api.js`
- Modify: `static/js/app.js`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `GET /api/v1/accounts` normalized list.
- Produces: account select, dynamic environment badge, real-risk warning and start confirmation.

- [ ] Add failing frontend-contract assertions for account selector and real warning dialog.
- [ ] Confirm failure.
- [ ] Implement account loading, selection/type binding and balance label.
- [ ] Add persistent red real-mode presentation and explicit start confirmation.
- [ ] Run frontend contract and Node syntax/tests.
- [ ] Commit real/demo UX.

### Task 5: VPS Compose and runbook

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/SECURITY.md`
- Modify: `deploy/DEPLOY.md`

**Interfaces:**
- Produces: explicit production environment and update procedure for `trade.solucoes-nexus.tech`.

- [ ] Propagate real-trading/security/domain variables explicitly in Compose.
- [ ] Document backup, environment update, config validation, rebuild, health check and demo-first validation.
- [ ] Validate `docker compose config --quiet` against the existing VPS-style env.
- [ ] Scan tracked files for known credentials and run `git diff --check`.
- [ ] Commit deployment readiness.

### Task 6: End-to-end verification

**Files:**
- Modify only if verification exposes a reproducible defect.

- [ ] Run all Python tests, Node tests, compileall and JavaScript syntax checks.
- [ ] Run Deriv read-only smoke for multiple symbols and a minimal demo lifecycle test.
- [ ] Restart the local API/orchestrator on port 8989.
- [ ] Browser-test same-mode symbol switching, account selector, marker behavior and console logs.
- [ ] Confirm no real order was sent and hand off the local URL plus VPS commands.


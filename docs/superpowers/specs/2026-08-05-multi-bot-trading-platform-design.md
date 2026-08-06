# NexusTrader Multi-Bot Platform Design

## Purpose

Transform the current single-process proof of concept into a reliable demo-trading platform that can orchestrate multiple robot instances from one web application. The first vertical slice keeps the existing Bollinger strategy, operates only on Deriv demo accounts, and proves the complete path from market data to chart, order lifecycle, persistence, controls, and recovery.

The design uses only the current Deriv API flow: REST account discovery, account-scoped OTP, and the WebSocket URL returned by that OTP request. No legacy authorization or legacy WebSocket endpoint is part of the runtime.

## Delivery Scope

This delivery must provide:

- A bot-instance model that supports multiple configured robots, even though the initial UI creates one default Bollinger instance.
- Real start and stop commands consumed by the trading runtime, with actual state and heartbeat shown in the app.
- An explicit demo-only execution guard. A real account may be listed, but cannot start or purchase contracts.
- Account-specific OTP connection without duplicate OTP requests.
- Automatic reconnect with heartbeat, request cleanup, and restoration of active subscriptions.
- Initial market history followed by live updates.
- A financial chart with:
  - line mode for tick/one-second views;
  - candlestick mode for 60-second and 300-second views;
  - Bollinger overlays;
  - entry, live-contract, exit, win, and loss annotations;
  - live, stale, reconnecting, offline, and last-updated states.
- Live trade lifecycle events from proposal through settlement.
- Idempotent trade persistence keyed by contract ID.
- Correct risk limits, including the configured consecutive-loss threshold.
- A responsive operational workspace for desktop and mobile.
- Automated tests for the connection lifecycle, risk boundary, orchestration commands, event validation, market aggregation, and API behavior.
- Development, deployment, operations, API, and architecture documentation.

## Deferred Scope

The following are deliberately separate follow-up projects:

- Real-money execution enablement.
- Strategy research and production validation for 1-minute and 5-minute contracts.
- Backtesting, walk-forward validation, paper portfolios, and parameter optimization.
- Multiple worker hosts and a distributed Redis/NATS event bus.
- User onboarding, OAuth, roles, and multi-tenant accounts.
- A visual strategy builder.

The interfaces introduced here must allow those projects without requiring another rewrite.

## Architecture

### Processes

1. **FastAPI control plane**
   - Serves the dashboard and REST API.
   - Persists configuration and desired state.
   - Receives validated internal runtime events.
   - Maintains bounded in-memory live buffers for fast chart snapshots.
   - Streams bot-scoped events to browsers over WebSocket.

2. **Bot orchestrator**
   - Polls persistent desired state.
   - Starts or stops one asynchronous `BotSession` per bot instance.
   - Owns the true runtime state and heartbeat.
   - Is the only component allowed to request proposals and buy contracts.

3. **Bot session**
   - Owns one Deriv account-scoped connection.
   - Owns market subscriptions, strategy state, money management, and risk state.
   - Publishes market, status, and trade events through a bounded runtime publisher.
   - Recovers open positions and active subscriptions after reconnect.

### Future Distribution Boundary

Runtime event publication is hidden behind an `EventPublisher` interface. This delivery uses a persistent internal HTTP publisher because both services run on the same Docker network. A later deployment may replace it with Redis Streams or NATS without changing strategy or session code.

Commands remain database-backed rather than pub/sub-only, so a restart cannot lose a requested stop or configuration change.

## Runtime Data Flow

```text
Deriv REST --accounts/OTP--> BotSession
Deriv WebSocket --history/ticks/contracts--> BotSession
BotSession --bounded event queue--> Internal API endpoint
Internal API --buffer + persist + broadcast--> Browser WebSocket
Browser --commands/config--> REST API --desired state--> SQLite
BotOrchestrator --poll desired state--> start/stop/reconfigure BotSession
```

## Connection Lifecycle

`NexusConnection.connect(account_id)` requests one OTP URL for the supplied account and connects directly to that URL. The connection keeps:

- a monotonically increasing request ID;
- pending request futures;
- a subscription registry containing stable keys, requests, and callbacks;
- one listener task;
- one reconnect task;
- one application heartbeat task.

When a connection closes:

1. Mark the connection unavailable and publish `reconnecting`.
2. Fail pending request futures instead of leaving them to time out.
3. Request a fresh OTP for the same account with exponential backoff and jitter.
4. Reconnect once; concurrent reconnect attempts are coalesced.
5. Replay every registered subscription request.
6. Replace remote subscription IDs while preserving stable local keys.
7. Publish `connected` only after subscriptions are restored.

Disconnect requested by the orchestrator must not start a reconnect.

## Bot Instance Model

Each bot instance has:

- stable ID and human-readable name;
- strategy ID and versioned JSON strategy configuration;
- Deriv account ID and account type;
- symbol;
- chart timeframe in seconds;
- contract duration and duration unit;
- initial stake;
- money-management mode: `fixed`, `martingale`, or `soros`;
- versioned JSON money-management configuration;
- risk configuration;
- desired state: `STOPPED` or `RUNNING`;
- runtime state: `STOPPED`, `STARTING`, `CONNECTING`, `RUNNING`, `PAUSED_RISK`, `RECONNECTING`, or `ERROR`;
- heartbeat timestamp and last error.

The first migrated instance is named `Bollinger Demo` and uses the existing settings and risk configuration.

## Safety Rules

- A session refuses to start when `account_type != demo`.
- A purchase is checked again immediately before sending `buy`.
- Stop is cooperative but authoritative: it prevents new proposals immediately, cancels market-only subscriptions, and continues monitoring already-open contracts until settled.
- A unique database constraint on `contract_id` makes settlement idempotent.
- Risk checks run before every proposal and again before every purchase.
- The consecutive-loss circuit breaker trips when losses are greater than or equal to the configured threshold.
- Daily limits use the configured `America/Sao_Paulo` business timezone.
- Stale market data blocks new entries.

## Market Data and Chart Contract

### History

On session start, request current-API `ticks_history`:

- tick/one-second mode: `style=ticks`;
- 60-second or 300-second mode: `style=candles` with matching granularity.

The initial history event contains no more than 1,000 points. The browser replaces the series once with this snapshot, then uses incremental updates.

### Live Events

Canonical market event fields:

```json
{
  "type": "market.tick",
  "bot_id": "uuid",
  "symbol": "R_100",
  "epoch": 1700000000,
  "price": 123.45,
  "bollinger": {"upper": 124.0, "middle": 123.2, "lower": 122.4}
}
```

The API aggregates ticks into OHLC candles using UTC epoch buckets. Late ticks may update the current bucket but cannot rewrite a closed bucket older than one interval. Buffers are ring buffers capped at 2,000 ticks and 1,000 candles per bot/timeframe.

### Visual Layers

| Layer | Job | Rendering | Failure behavior |
| --- | --- | --- | --- |
| Price | Current and historical movement | Line for tick/1s; candles for 60s/300s | Keep last good data and mark stale |
| Bollinger | Strategy context | Three directly keyed line series | Hide until enough points exist |
| Active trade | Show entry and current contract | Entry marker, price line, countdown/status rail | Persist during reconnect |
| Settlement | Show outcome | Exit marker and win/loss label | Rebuilt from persisted trades |
| Connection | Trust indicator | Live/stale/reconnecting/offline text with timestamp | Never infer Deriv state from browser socket alone |

Lightweight Charts 5.x owns the central Canvas renderer. The page keeps one focal chart visible at a time. Desktop uses a compact top bar, a left bot/config rail, central chart, and right live-operation rail. Mobile portrait opens with status and chart first; configuration and details use drawers or collapsible sections. Mobile landscape is the preferred detailed chart mode.

## Frontend Product Direction

The existing provisional dashboard is replaced by an algorithmic day-trading workstation. It must feel like an operational product rather than an admin dashboard or a grid of generic cards.

### Desktop Shell

- **Command bar:** NexusTrader identity, environment badge (`DEMO`), selected account, Deriv/runtime latency, global emergency stop, and compact user/session controls.
- **Bot rail:** searchable robot list, runtime status, strategy name, symbol, today PnL, create/duplicate controls, and the selected robot's configuration launcher.
- **Chart workspace:** dominant price chart, symbol and timeframe controls, line/candle selector derived from timeframe, indicator toggles, live/stale timestamp, crosshair data, and a return-to-live action.
- **Operation rail:** active-contract card with direction, stake, entry, current spot, countdown, indicative PnL, risk state, and a chronological event tape.
- **Bottom dock:** recent operations, open/closed filters, result, contract ID, duration, entry/exit values, profit, strategy, and expandable diagnostics.

The layout uses rails, dividers, bands, and one dominant viewport rather than nested equal-weight cards.

### Robot Configuration Flow

Selecting `Configure robot` opens a focused side sheet with these ordered groups:

1. identity and strategy;
2. account, symbol, chart timeframe, and contract duration;
3. strategy parameters;
4. stake and money management (`fixed`, `martingale`, `soros`);
5. stop loss, take profit, maximum stake, maximum trades, and consecutive losses;
6. validation summary and start action.

Unsafe or internally inconsistent combinations are rejected before saving. Real accounts appear disabled with a `Demo validation only` explanation.

### Visual Language

- Near-black neutral workspace with restrained cool-gray structure.
- Deriv red is reserved for brand and destructive/stop actions, not every primary button.
- Green and red encode positive/negative trading outcomes with redundant arrows and text.
- Cyan/blue encodes live connection and selected analytical context.
- Amber encodes reconnecting, stale data, and risk pauses.
- Compact tabular typography is used for prices, time, PnL, and IDs.
- Animation is limited to evidence-bearing changes: incoming price, countdown, connection transition, new event, and trade settlement.

### First-Scan Contract

Before touching a control, the user must be able to answer:

- Which robot is selected and is it truly running?
- Is the Deriv market stream live, stale, reconnecting, or offline?
- What symbol and timeframe are displayed?
- Is there an active operation, where did it enter, and how long remains?
- What is today's realized PnL and current risk state?

### Mobile Contract

Mobile portrait opens with command/status bar, robot selector, active-operation summary, and chart. Robot list, configuration, indicators, and history use bottom sheets. The chart must remain reachable without scrolling through configuration fields. Mobile landscape expands the chart and shows a compact operation rail.

### Frontend Modules

- `static/index.html`: semantic application shell and templates.
- `static/styles.css`: tokens, responsive shell, rails, sheets, tables, and states.
- `static/js/api.js`: authenticated REST client and error normalization.
- `static/js/store.js`: selected bot, configuration, connection, market, trade, and UI state.
- `static/js/chart.js`: Lightweight Charts lifecycle, series, markers, and resizing.
- `static/js/app.js`: orchestration, rendering, commands, sheets, and reconnect behavior.

The frontend remains build-free ES modules for this vertical slice. This avoids introducing a Node build pipeline while establishing module boundaries that can later move to React/TypeScript if product complexity justifies it.

## API Surface

Primary endpoints:

- `GET /api/v1/bots`
- `POST /api/v1/bots`
- `GET /api/v1/bots/{bot_id}`
- `PUT /api/v1/bots/{bot_id}`
- `POST /api/v1/bots/{bot_id}/start`
- `POST /api/v1/bots/{bot_id}/stop`
- `GET /api/v1/bots/{bot_id}/trades`
- `GET /api/v1/bots/{bot_id}/market/history`
- `GET /api/v1/strategies`
- `WS /api/v1/ws/live?bot_id={bot_id}`
- `POST /api/v1/internal/events` protected by `X-Internal-Token`

Legacy singular endpoints remain temporarily as adapters for the default bot and are documented as deprecated.

## Event Types

- `runtime.status`
- `market.history`
- `market.tick`
- `trade.proposal`
- `trade.opened`
- `trade.updated`
- `trade.closed`
- `risk.blocked`
- `system.error`

Every event includes `event_id`, `bot_id`, `type`, `epoch`, and `schema_version`. Trade events also include `contract_id`. The API de-duplicates event IDs in a bounded cache.

## Authentication and Exposure

This delivery introduces two separate controls:

- `INTERNAL_API_TOKEN` protects runtime-to-API events and is required.
- `DASHBOARD_API_KEY` protects control and account endpoints when configured.

Because the existing production dashboard does not yet have a user-login flow, dashboard authentication is API-key based for this vertical slice. The browser stores it only in session storage. The deployment guide requires configuring it before exposing the app. Public static assets and the root page remain accessible; account, configuration, trade, command, documentation, and schema endpoints are protected.

The Deriv PAT never enters frontend HTML, browser storage, logs, or URLs.

## Error and Degradation Behavior

- Deriv disconnected: chart retains data, state becomes reconnecting, new entries stop.
- API unavailable: publisher queue retains a bounded recent window; trading pauses if status events cannot be delivered for a configured interval.
- Browser disconnected: backend trading continues; browser restores snapshot on reconnect.
- Database locked: retry with existing busy timeout; fail command visibly after timeout.
- Invalid configuration: API rejects it with field-level errors and preserves previous configuration.
- Open contract during requested stop: no new trade begins; monitoring continues to settlement.
- Stale heartbeat: API reports runtime as stale instead of running.

## Testing Strategy

1. **Unit tests**
   - circuit-breaker threshold;
   - OHLC aggregation and late ticks;
   - bot configuration validation;
   - demo-only execution guard;
   - event validation and deduplication.

2. **Async component tests**
   - one OTP request per connection;
   - subscription replay after reconnect;
   - pending request failure on disconnect;
   - orchestrator start/stop transitions;
   - bounded publisher queue.

3. **API tests**
   - authorization boundaries;
   - bot CRUD and commands;
   - internal token protection;
   - live snapshot and bot-scoped WebSocket routing.

4. **Browser verification**
   - history renders before live events;
   - line/candle switching;
   - stale and reconnect states;
   - entry/update/exit annotations;
   - responsive desktop, mobile portrait, and mobile landscape.

5. **Production smoke test**
   - demo account only;
   - observe live ticks for at least two minutes;
   - force a WebSocket reconnect and confirm subscriptions return;
   - start and stop the bot from the app;
   - confirm no new proposal occurs after stop;
   - observe one complete demo trade lifecycle.

## Documentation Set

- `docs/ARCHITECTURE.md`: component boundaries and data flows.
- `docs/API.md`: REST, WebSocket, authentication, and event contracts.
- `docs/DEVELOPMENT.md`: environment, tests, and local execution.
- `docs/OPERATIONS.md`: deployment, secrets, health checks, logs, backup, and incident steps.
- `docs/ROADMAP.md`: subsequent multi-strategy, backtesting, research, and distribution phases.
- `docs/DERIV-API-REFERENCE.md`: updated to reference only current runtime endpoints.

## Acceptance Criteria

The vertical slice is accepted when all automated tests pass and a demo instance can be selected, configured, started, observed with live chart data, stopped for new entries, and followed through an already-open contract until a single persisted settlement. A forced Deriv disconnect must recover the market stream without restarting the container. No browser-visible state may claim that the Deriv feed or robot is running unless the runtime heartbeat and connection state confirm it.

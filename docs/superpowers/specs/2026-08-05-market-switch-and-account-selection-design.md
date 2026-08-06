# Market Switching and Account Selection Design

## Scope

Correct chart corruption after a symbol/timeframe change, reduce trade annotations to one entry and one settlement marker, allow the operator to select any Deriv account returned by the current REST API, and prepare the existing Docker deployment for controlled real/demo switching.

## Decisions

### Chart context

The chart context is the tuple `(symbol, timeframe_seconds, mode)`. When any member changes, all Lightweight Charts series, Bollinger data, price lines and markers are recreated before accepting the new history. A history refresh in the same context replaces price data and clears Bollinger data without duplicating markers. Frontend and API read model reject market ticks whose symbol/timeframe do not match the active history context.

During a saved configuration change, the UI displays `TROCANDO MERCADO` and does not render the old snapshot under the new symbol. The context becomes active only after a matching `market.history`. Saving selects the bot once, eliminating the current double subscription window.

### Trade annotations

Markers are keyed by `entry:{contract_id}` and `exit:{contract_id}`. `trade.opened` creates the entry marker, `trade.updated` only refreshes the operation panel and price line, and `trade.closed` creates one `WIN` or `LOSS` marker. Repeated or replayed events update existing keys instead of appending.

### Account selection and real-trading safety

FastAPI exposes a normalized, authenticated account catalog from `GET /trading/v1/options/accounts`: `account_id`, `account_type`, currency, balance and status. The configuration drawer uses this catalog instead of a free-text account ID and derives `account_type` from the selected option.

Real-account execution requires all of the following:

1. `ALLOW_REAL_TRADING=true` in the server environment;
2. a Deriv account classified by the API as `real`;
3. persisted bot `account_type=real` matching that returned account;
4. explicit confirmation in the dashboard before start;
5. a final executor check immediately before `buy`.

Demo remains the safe default. Automated validation never submits a real proposal/buy.

### Deployment

Compose propagates `ALLOW_REAL_TRADING`, both security keys, `DOMAIN` and the internal API URL explicitly. The existing Traefik labels continue to serve `trade.solucoes-nexus.tech`. The runbook provides a safe update sequence: stop bots, backup SQLite, update code/env, validate Compose, rebuild, health check, and test demo before selecting real.

## Acceptance criteria

- Switching between two line-mode symbols or two candle-mode symbols leaves no series data from the previous market.
- Old ticks cannot be appended after a context switch.
- Twenty `trade.updated` events still render one entry marker; close adds exactly one result marker.
- The account selector lists both available real and demo accounts with type/balance.
- Real account creation/start is rejected when the environment flag is false and accepted when true.
- Persisted type/account mismatch is rejected before a socket or buy.
- Full Python tests, Node tests, syntax checks, Compose validation, demo Deriv smoke and browser QA pass.


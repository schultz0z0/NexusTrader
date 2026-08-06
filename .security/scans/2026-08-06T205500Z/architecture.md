# NexusTrader security architecture

## Trust boundaries

1. Browser operator -> FastAPI control plane over HTTPS/WebSocket.
2. FastAPI -> shared SQLite volume.
3. Bot runtime -> Deriv REST for account/OTP and authenticated Deriv WebSocket.
4. Bot runtime -> internal FastAPI event endpoint using an internal token.
5. Bot runtime -> optional Telegram/Discord services.
6. Internet -> Traefik -> API container; the bot container is not directly published.

## Sensitive assets

- Deriv PAT and OTP URL;
- dashboard control key and internal event token;
- Telegram token;
- selected account identity and trading configuration;
- open-contract ownership, trade journal and risk state.

## Entry points

- dashboard and REST routes under /api/v1;
- bot-specific WebSocket;
- internal event ingestion route;
- Deriv REST/WebSocket responses;
- persisted SQLite configuration read by the orchestrator.

## Security controls verified

- authenticated REST control routes;
- demo-only backend guard unless real trading is explicitly enabled;
- parameterized SQLite statements;
- closed CORS configuration;
- one-time, bot-scoped WebSocket tickets;
- framing protection;
- fail-closed secrets outside explicit loopback development mode.

## Operational note

Production was inspected passively. Active trading and destructive probes were limited
to localhost against the Deriv DEMO account.

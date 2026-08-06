# API NexusTrader

Base: `/api/v1`. Quando `DOMAIN` está configurado, `DASHBOARD_API_KEY` e `INTERNAL_API_TOKEN` são obrigatórios no startup.

## Autenticação

Rotas do dashboard recebem `X-API-Key: <DASHBOARD_API_KEY>`. Antes de abrir o WebSocket,
o frontend solicita um ticket curto e de uso único em `POST /ws-tickets/{bot_id}`; a
chave permanente não entra na URL. A rota de eventos entre containers aceita apenas
`X-Internal-Token`.

## Robôs

| Método | Rota | Função |
|---|---|---|
| GET | `/bots` | Listar instâncias |
| GET | `/accounts` | Listar contas Deriv normalizadas (`demo`/`real`) e saldo atual |
| POST | `/bots` | Criar instância demo ou real conforme flag do servidor |
| GET | `/bots/{bot_id}` | Obter configuração/estado |
| PUT | `/bots/{bot_id}` | Substituir configuração e incrementar revisão |
| DELETE | `/bots/{bot_id}` | Excluir uma instância parada |
| POST | `/bots/{bot_id}/start` | Persistir `desired_state=RUNNING` |
| POST | `/bots/{bot_id}/stop` | Solicitar parada segura |
| GET | `/bots/{bot_id}/trades?limit=100` | Journal persistente |
| GET | `/bots/{bot_id}/snapshot` | Read model ao vivo |

Exemplo de criação:

```json
{
  "name": "Donchian R_75 — 1m",
  "strategy_id": "donchian",
  "strategy_config": {},
  "account_id": "DOT123456",
  "account_type": "demo",
  "symbol": "R_75",
  "timeframe_seconds": 60,
  "duration": 2,
  "duration_unit": "m",
  "initial_stake": 1.0,
  "money_management": "fixed",
  "money_config": {},
  "risk_config": {
    "take_profit_daily": 25,
    "stop_loss_daily": 10,
    "max_daily_trades": 30,
    "max_single_stake": 4,
    "max_consecutive_losses": 3,
    "cooldown_minutes": 15
  }
}
```

## WebSocket

Primeiro solicite `POST /api/v1/ws-tickets/{bot_id}` com `X-API-Key`. Em seguida,
`GET /api/v1/ws/bots/{bot_id}?ticket=<ticket>` envia:

```json
{"type":"snapshot","bot_id":"...","data":{"market":{},"last_tick":null,"active_trade":null,"recent_trades":[]}}
```

Depois transmite apenas eventos daquele robô: `market.history`, `market.tick`, `runtime.status`, `strategy.signal`, `risk.blocked`, `trade.opened`, `trade.updated` e `trade.closed`.

Eventos de mercado cujo `symbol` ou `timeframe_seconds` não correspondem à configuração selecionada são descartados. Atualizações repetidas de contrato atualizam o P&L e o spot, mas não criam novos marcadores no gráfico.

No dashboard, `/accounts` alimenta um seletor global. A escolha é aplicada às configurações paradas e não pode ser trocada durante `STARTING`, `RUNNING` ou `STOPPING`; o backend continua tratando `account_id` e `account_type` em cada robô para persistência, recuperação e validação independente do navegador.

## Interno

`POST /api/v1/internal/events` recebe envelopes com `event_id`, `schema_version`, `type`, `bot_id` e `epoch`. IDs repetidos são aceitos como duplicados e não reaplicados.

## Saúde e compatibilidade

`GET /api/v1/health` valida que API e banco respondem. Rotas `/api/v1/bot/*`, `/config/risk` e `/trades/*` permanecem como fachada de compatibilidade para o primeiro robô, mas integrações novas devem usar `/bots`.

# API NexusTrader

Base: `/api/v1`. Quando `DOMAIN` está configurado, `DASHBOARD_API_KEY` e `INTERNAL_API_TOKEN` são obrigatórios no startup.

## Autenticação

Rotas do dashboard recebem `X-API-Key: <DASHBOARD_API_KEY>`. Antes de abrir o WebSocket,
o frontend solicita um ticket curto e de uso único em `POST /ws-tickets/{bot_id}`; a
chave permanente não entra na URL. A rota de eventos entre containers aceita apenas
`X-Internal-Token`. Aprovação, reanálise e rollback do NexusTrade exigem ainda
`X-Nexus-Human-Key`, configurada por `NEXUS_HUMAN_ACTION_KEY`; o ator auditado é
derivado no servidor de `NEXUS_HUMAN_ACTOR`, nunca do corpo da requisição.

## Robôs

| Método | Rota | Função |
|---|---|---|
| GET | `/bots` | Listar instâncias |
| GET | `/accounts` | Listar contas Deriv normalizadas (`demo`/`real`) e saldo atual |
| POST | `/bots` | Criar instância demo ou real conforme flag do servidor |
| GET | `/bots/{bot_id}` | Obter configuração/estado |
| PUT | `/bots/{bot_id}` | Substituir configuração e incrementar revisão |
| DELETE | `/bots/{bot_id}` | Excluir uma instância parada |
| POST | `/bots/{bot_id}/real-confirmation` | Emitir ticket REAL após frase exata |
| POST | `/bots/{bot_id}/start` | Persistir RUNNING; REAL exige `real_ticket` |
| POST | `/bots/{bot_id}/stop` | Solicitar parada segura |
| POST | `/bots/stop-all` | Parada global persistente e revogação de tickets REAL |
| GET | `/bots/{bot_id}/trades?limit=100` | Journal persistente |
| GET | `/bots/{bot_id}/order-intents?limit=100` | Auditoria de ownership de compras |
| GET | `/bots/{bot_id}/snapshot` | Read model ao vivo |

Exemplo de criação:

```json
{
  "name": "Donchian R_75 — 1m",
  "strategy_id": "donchian",
  "strategy_config": {"period":21,"deviation":1,"depth":15,"backstep":3},
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

Trades ativos expõem `lifecycle_state` com `live` ou `awaiting_settlement`; o payload
terminal usa `closed`. Um contrato expirado permanece ativo enquanto `is_sold=0`, mesmo
que tenha `status="open"` e P&L provisório. A assinatura é respaldada por consultas
pontuais `proposal_open_contract` após a expiração, e somente `is_sold=1` produz
`trade.closed`. O navegador mostra **AGUARDANDO LIQUIDAÇÃO** nesse intervalo, não soma o
resultado provisório ao journal e só limpa `active_trade` no fechamento terminal.

`desired_state` é a intenção de controle persistida. `runtime_state` é a última
telemetria publicada pela sessão e pode permanecer, por exemplo, em `STOPPING` após um
restart no qual `desired_state=STOPPED` impede corretamente a criação de uma nova
sessão. O endpoint `/bots/{bot_id}/trades` é o journal histórico autoritativo;
`snapshot.active_trade` e `snapshot.recent_trades` pertencem ao read model descartável
do runtime.

No dashboard, `/accounts` alimenta um seletor global. A escolha é aplicada às configurações paradas e não pode ser trocada durante `STARTING`, `RUNNING` ou `STOPPING`; o backend continua tratando `account_id` e `account_type` em cada robô para persistência, recuperação e validação independente do navegador.

## Interno

`POST /api/v1/internal/events` recebe envelopes com `event_id`, `schema_version`, `type`, `bot_id` e `epoch`. IDs repetidos são aceitos como duplicados e não reaplicados.

O ingress de produção exclui esse prefixo; ele continua disponível apenas na rede
interna Docker e exige `X-Internal-Token`.

## Saúde e compatibilidade

- `GET /health/live`: processo HTTP vivo.
- `GET /health/ready`: banco e todos os componentes de bots desejados RUNNING.
- `GET /health`: alias da readiness.

Rotas legadas permanecem para DEMO; start REAL só existe no fluxo versionado com ticket.

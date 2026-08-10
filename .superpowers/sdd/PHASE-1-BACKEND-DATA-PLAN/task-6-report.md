# Task 6 — API protegida, eventos e snapshots

## RED observado

Todos os testes foram executados pela `.venv`, com credenciais dummy no processo,
`DEV_MODE=false`, `ALLOW_REAL_TRADING=false` e `REAL_MAX_STAKE_USD=0`.

O primeiro RED de `tests.test_nexus_trade_api tests.test_control_plane` executou 25
testes e terminou com 10 failures e 3 errors, todos ligados ao contrato novo:

- os endpoints `/api/v1/nexus-trade*` ainda devolviam 404, inclusive antes da
  autenticação;
- o snapshot WebSocket não continha runtime/lanes/versionamento Nexus;
- `LiveStore` não sanitizava nem materializava eventos `nexus.*`;
- o PUT do singleton chegava ao SQLite e vazava `IntegrityError`, em vez de 409;
- o runtime não aplicava `emergency_stop` persistido aos dispatchers antes de uma
  troca de modo adiada.

REDs incrementais provaram ainda que evento com revisão antiga era aceito e que os
oito eventos Nexus não eram classificados como críticos pelo publisher. Cada falha
foi observada antes da respectiva implementação.

## Contratos REST, autenticação e REAL

Todas as rotas usam a mesma dependência `require_dashboard_key` do control plane:

- `GET /api/v1/nexus-trade`: snapshot composto e versionado;
- `POST /api/v1/nexus-trade/mode` (PUT compatível): persiste OFF/DEMO, ON/DEMO ou
  ON/REAL somente no runtime do Champion;
- `POST /api/v1/nexus-trade/emergency-stop`: persiste e publica a parada ou liberação
  total;
- `POST /api/v1/nexus-trade/real-confirmation`: usa frase exata, flag, teto e ticket
  curto de uso único vinculado à conta e à revisão do singleton;
- `GET /versions`, `/campaigns`, `/reports`, `/proposals` e `/exports`: somente leitura
  de dados existentes; relatórios, propostas e exports retornam listas vazias válidas
  nesta fase.

ON/REAL permanece fail-closed por `ALLOW_REAL_TRADING`, `REAL_MAX_STAKE_USD` e pelo
`RealStartTicketStore` existente. Toda mudança de modo ou emergency revoga tickets.
Nenhum teste habilitou, conectou ou operou conta REAL.

O CRUD comum rejeita criação com `strategy_id=nexus_trade` em 422 e rejeita update,
delete, start, stop e confirmação REAL comum do ID `nexus-trade` em 409. A estratégia
reservada não aparece em `/api/v1/strategies`.

## Snapshot, runtime e eventos

O snapshot contém `schema_version`, `snapshot_version`, runtime persistido, estado de
emergência, lanes Champion/Trial com versões, campanha ativa, decisões, trades,
reports e proposals. Startup, GET e abertura do WebSocket hidratam o read model a
partir do SQLite; mudanças de modo avançam a revisão do singleton.

O runtime contínuo recarrega o snapshot no boundary antes de processar compras.
`emergency_stop` é aplicado imediatamente a dispatchers atuais e também ao dispatcher
preparado para uma troca adiada, impedindo compra enquanto o Champion não está IDLE.

Eventos suportados, com `event_id`, schema e revisão:

- `nexus.runtime`
- `nexus.decision`
- `nexus.trade`
- `nexus.campaign`
- `nexus.report`
- `nexus.trial_changed`
- `nexus.proposal`
- `nexus.version_changed`

Todos são críticos para backpressure. `event_id` duplicado e revisão anterior são
ignorados; revisão igual permite eventos distintos da mesma transação. Antes de
armazenar ou transmitir, o backend remove recursivamente tickets, tokens, secrets,
credenciais, OTPs, autorização e caminhos locais. `/exports` não recebe nem resolve
caminho informado pelo cliente e não expõe o manifesto local de ticks.

## Testes e resultados

| Verificação | Resultado |
| --- | --- |
| GREEN Task 6 + control plane/API | 32 testes, PASS |
| regressão crítica API/repository/runtime/orchestrator/publisher | 89 testes, PASS |
| suíte Python completa final | 350 testes, PASS em 27,980s |
| testes JavaScript | 17 testes, PASS |
| `python -m compileall -q api backtest core data database risk strategies trading nexus_trade` | PASS |
| `python -m pip check` | PASS (`No broken requirements found`) |
| `git diff --check` | PASS |
| diff protegido Donchian/Nexus Speed/`utils/indicators.py` | vazio |

A suíte emite somente o `StarletteDeprecationWarning` preexistente da integração
`httpx`/`TestClient`. Logs de conexão vêm de doubles dos testes; não houve rede
externa, deploy, push, PR, chamada GitHub ou operação REAL.

## Commit e preocupações

Commit local exigido: `feat: expose protected NexusTrade control plane`.

As coleções futuras continuam deliberadamente vazias até as Tasks 7–9; não foi
implementado treino, candidato, promoção, relatório ou geração de export nesta task.
O modo persistido é aplicado pelo runtime no próximo boundary causal, e a parada de
emergência é verificada antes de qualquer processamento/compra. A trilha REAL não
recebeu smoke test por restrição explícita e continua protegida pelos gates atuais.

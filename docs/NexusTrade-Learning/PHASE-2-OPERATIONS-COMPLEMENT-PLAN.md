# NexusTrade Live Operations Complement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execution was explicitly fixed by the user as inline, in one session, without subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expor gráfico, decisões, posições e journal ao vivo do NexusTrade e permitir configurar o gerenciamento do Champion ON sem alterar a estratégia protegida.

**Architecture:** O feed R_100 já publica `market.history` e `market.tick` pelo pipeline comum; o cliente NexusTrade passará a consumi-los em um store operacional dedicado. Posições usam `nexus.position`, com snapshot reconstruído dos lane heads, e liquidações continuam em `nexus.trade`. O gerenciamento do Champion vive numa tabela protegida separada do singleton canônico e é aplicado pelo runtime somente numa fronteira IDLE.

**Tech Stack:** FastAPI, aiosqlite/SQLite WAL, runtime asyncio, eventos HTTP/WebSocket, HTML/CSS, JavaScript ES modules, Lightweight Charts, Python unittest e Node test runner.

## Global Constraints

- `STRATEGY.md`, `PRD.md`, `FRONTEND.md` e `ML-GOVERNANCE.md` são fontes de verdade.
- R_100, M1, 58 segundos, Bollinger obrigatória e ADX `<= 22` permanecem imutáveis.
- OFF mantém Champion e Trial em DEMO a USD 0,35; gerenciamento é usado somente pelo Champion ON.
- Trial nunca recebe controles de configuração.
- No máximo um contrato por lane; Champion e Trial permanecem independentes.
- Configuração só muda com Champion OFF e lane Champion IDLE.
- Nenhuma UI otimista para configuração, modo, posição ou liquidação.
- REAL permanece bloqueado durante implementação e QA.
- Donchian e Nexus Speed não sofrem mudança comportamental.
- Nenhuma credencial, conta completa, token, ticket ou caminho local entra em DOM, eventos ou logs.

---

### Task 1: Gerenciamento persistido e protegido do Champion

**Files:**
- Modify: `database/nexus_models.py`
- Modify: `nexus_trade/repository.py`
- Modify: `database/repository.py`
- Modify: `api/routes/nexus_trade.py`
- Modify: `nexus_trade/runtime.py`
- Modify: `tests/test_nexus_trade_repository.py`
- Modify: `tests/test_nexus_trade_api.py`
- Modify: `tests/test_nexus_trade_runtime.py`

**Interfaces:**
- Produces: `get_champion_management()`, `set_champion_management(expected_revision, payload)` e `POST /api/v1/nexus-trade/champion-management`.
- Snapshot: `champion_management={revision, initial_stake, money_management, money_config, risk_config}`.

- [ ] **Step 1: Escrever RED de schema, defaults, validação e CAS**

Provar default `fixed/USD 0.35`, persistência após restart, 409 em revisão vencida,
422 para NaN/infinito/negativo/modelo desconhecido e rollback integral por falha injetada.

- [ ] **Step 2: Executar RED**

Run: `python -m unittest tests.test_nexus_trade_repository tests.test_nexus_trade_api -v`

Expected: falhas por tabela, métodos e rota ausentes.

- [ ] **Step 3: Implementar schema e repository**

Criar `nexus_champion_management` com uma linha `bot_id=nexus-trade`, revisão positiva,
JSON canônico e checks de domínio. `set_champion_management` usa `BEGIN IMMEDIATE`,
confere Champion OFF e lane head IDLE, CAS da revisão e avanço de `config_revision`.

- [ ] **Step 4: Implementar rota fina e envelope**

Payload Pydantic contém `expected_revision`, `initial_stake`, `money_management`,
`money_config` e `risk_config`. Resposta publica `nexus.runtime` somente após commit.

- [ ] **Step 5: Escrever RED de runtime seguro**

Provar que OFF ignora gerenciamento e usa 0,35; ON usa a revisão persistida; mudança é
adiada com RESERVED/ACTIVE/QUARANTINED; restart restaura stake/Soros/Martingale; cap
REAL bloqueia stake inicial e stake calculada acima do servidor.

- [ ] **Step 6: Implementar aplicação do gerenciamento**

Adicionar `_champion_management`, recriar `MoneyManager` e dispatchers somente em
fronteira IDLE. Todos os pontos de stake/settlement usam a configuração aplicada, sem
alterar `bot_instances` canônico.

- [ ] **Step 7: Rodar GREEN e regressão focada**

Run: `python -m unittest tests.test_nexus_trade_repository tests.test_nexus_trade_api tests.test_nexus_trade_runtime tests.test_control_plane -v`

- [ ] **Step 8: Commit**

Run: `git commit -m "feat: configure protected NexusTrade champion management"`

### Task 2: Contrato operacional de mercado e posição

**Files:**
- Modify: `api/live_store.py`
- Modify: `nexus_trade/repository.py`
- Modify: `nexus_trade/runtime.py`
- Modify: `tests/test_nexus_trade_runtime.py`
- Modify: `tests/test_nexus_trade_api.py`
- Modify: `tests/test_control_plane.py`

**Interfaces:**
- Consumes: `market.history` e `market.tick` existentes para `bot_id=nexus-trade`.
- Produces: `nexus.position` com identidade `(lane, contract_id, update_epoch, status)` e `positions` no snapshot.

- [ ] **Step 1: Escrever RED de envelopes e reconstrução**

Provar que history/tick continuam genéricos e sanitizados, `nexus.position` exige lane,
contract id inteiro positivo, status `OPEN|UPDATED|CLOSED`, epoch monotônico e payload
sem conta/token. Snapshot após restart reconstrói posição ACTIVE pelo lane head; IDLE
não cria posição fantasma.

- [ ] **Step 2: Executar RED**

Run: `python -m unittest tests.test_nexus_trade_runtime tests.test_nexus_trade_api tests.test_control_plane -v`

- [ ] **Step 3: Implementar posição aberta e updates**

Após receipt persistido, publicar `nexus.position OPEN`. Passar `on_update_callback`
ao `ContractMonitor` para publicar `UPDATED` com spot, profit, buy price e expiry
sanitizados. Settlement persistido publica `CLOSED` e `nexus.trade` na ordem causal.

- [ ] **Step 4: Implementar snapshot durável mínimo**

Ler os lane heads numa única transação e expor state/owner por lane. Derivar `positions`
somente de states ACTIVE/QUARANTINED com contract id válido; dados indisponíveis ficam
nulos e recebem `RECONCILING`, nunca são inventados.

- [ ] **Step 5: GREEN, cancelamento e idempotência**

Cobrir cancel durante update, duplicata de evento, update posterior ao CLOSED, restart
ACTIVE, duas lanes simultâneas e falha de publisher sem atraso/retry de compra.

- [ ] **Step 6: Commit**

Run: `git commit -m "feat: publish NexusTrade live market positions"`

### Task 3: Store e modelo visual operacional

**Files:**
- Modify: `static/js/nexus_trade_store.js`
- Create: `static/js/nexus_trade_operations.js`
- Modify: `static/js/chart.js`
- Modify: `static/js/app.js`
- Modify: `tests/js/nexus_trade_store.test.mjs`
- Create: `tests/js/nexus_trade_operations.test.mjs`
- Modify: `tests/js/chart.test.mjs`

**Interfaces:**
- Produces: `buildNexusOperationsModel(state)`, `createNexusOperationsController(options)` e métodos de markers/linhas no `TradingChart`.

- [ ] **Step 1: Escrever RED do store**

Provar ingestão de `market.history`, `market.tick` e `nexus.position`; rejeição de
mercado diferente de R_100/M1; updates fora de ordem; CLOSED terminal; reconnect que
preserva last-known-good e bloqueia ações até snapshot.

- [ ] **Step 2: Escrever RED do modelo visual**

Mapear state machine, ADX, gate ML, reason codes, contrato, countdown, latência e
journal para textos em português. Filtrar TODAS/CHAMPION/TRIAL/BLOQUEADAS sem alterar
o store.

- [ ] **Step 3: Executar RED**

Run: `node --test tests/js/nexus_trade_store.test.mjs tests/js/nexus_trade_operations.test.mjs tests/js/chart.test.mjs`

- [ ] **Step 4: Implementar store/model/controller**

Manter `market`, `positions`, `operationFilter` e status live/stale. Controller possui
uma única instância de chart, destrói listeners no unmount e limita markers/journal.

- [ ] **Step 5: Estender TradingChart**

Adicionar Bollinger histórica e corrente, markers por lane/reason e API de clear sem
alterar Donchian/EMA existentes. Vela parcial recebe dado de tick, nunca marker causal.

- [ ] **Step 6: Rodar GREEN JS completo**

Run: `node --test tests/js/*.test.mjs`

- [ ] **Step 7: Commit**

Run: `git commit -m "feat: model NexusTrade live operations"`

### Task 4: Workspace operacional e formulário de gerenciamento

**Files:**
- Modify: `static/index.html`
- Modify: `static/styles.css`
- Modify: `static/js/nexus_trade_api.js`
- Modify: `static/js/nexus_trade_view.js`
- Modify: `static/js/app.js`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/js/nexus_trade_api.test.mjs`
- Modify: `tests/js/nexus_trade_view.test.mjs`
- Modify: `tests/js/nexus_trade_actions.test.mjs`

**Interfaces:**
- Produces: gráfico R_100/M1, cards Champion/Trial, filtros/journal e dialog `CONFIGURAR GERENCIAMENTO`.

- [ ] **Step 1: Escrever RED de contrato HTML e acessibilidade**

Exigir IDs do gráfico, ADX, dois cards, duas regiões live, filtros, journal, botão e
dialog. Strategy/symbol/timeframe/duration não aparecem como campos editáveis. Dialog
possui labels, foco preso, Escape, restauração de foco e touch target de 44 px.

- [ ] **Step 2: Escrever RED de API/formulário**

Provar header normal, CAS, erro 409 com reidratação, 422 sem mutação, campos por modelo,
disabled quando ON/não-IDLE e ausência de configuração Trial.

- [ ] **Step 3: Executar RED**

Run: `python -m unittest tests.test_frontend_contract -v`

Run: `node --test tests/js/nexus_trade_api.test.mjs tests/js/nexus_trade_view.test.mjs tests/js/nexus_trade_actions.test.mjs`

- [ ] **Step 4: Implementar markup e CSS responsivo**

Desktop: chart principal + lanes; mobile: status, chart, Champion, Trial, filtros e
journal. Reutilizar tokens, tipografia e componentes existentes.

- [ ] **Step 5: Implementar gerenciamento**

Formulário mostra Fixed/Martingale/Soros e campos condicionais. Salva apenas a resposta
confirmada, limpa dados transitórios, reidrata em conflito e mostra revisão aplicada.

- [ ] **Step 6: Implementar render operacional sem F5**

Renderizar history/tick, ADX, markers, contratos, countdown e journal a cada evento
aceito. Estados stale/reconnecting mantêm evidência e desabilitam ações.

- [ ] **Step 7: Rodar GREEN e regressão frontend**

Run: `node --check static/js/app.js`

Run: `node --test tests/js/*.test.mjs`

Run: `python -m unittest tests.test_frontend_contract tests.test_nexus_trade_api -v`

- [ ] **Step 8: Commit**

Run: `git commit -m "feat: show NexusTrade live operations workspace"`

### Task 5: Documentação, Docker, navegador e gate final

**Files:**
- Modify: `docs/NexusTrade-Learning/FRONTEND.md`
- Modify: `docs/NexusTrade-Learning/PHASE-2-IMPLEMENTATION-RUNBOOK.md`
- Modify: `docs/NexusTrade-Learning/PHASE-2-VALIDATION.md`
- Modify: `docs/NexusTrade-Learning/PHASE-2-OPERATIONS-COMPLEMENT-PLAN.md`

**Interfaces:**
- Produces: registro GO/NO-GO e procedimentos localhost/VPS atualizados.

- [ ] **Step 1: Rodar suíte integral**

Run: `python -m unittest discover -s tests -v`

Run: `node --test tests/js/*.test.mjs`

Run: `node --check static/js/app.js`

Run: `python -m compileall -q api core data database nexus_trade strategies trading tests`

Run: `python -m pip check`

Run: `docker compose config --quiet`

- [ ] **Step 2: Rebuild Docker isolado DEMO-only**

Usar porta 8993, volume da validação local, `ALLOW_REAL_TRADING=false`,
`REAL_MAX_STAKE_USD=0` e chaves locais distintas. Confirmar API/bot healthy e restart 0.

- [ ] **Step 3: Validar navegador real**

Observar history/ticks R_100, Bollinger, ADX, decisões bloqueadas, uma posição por lane,
liquidação/journal, configuração Fixed/Martingale/Soros e persistência após restart.

- [ ] **Step 4: Validar matriz responsiva e acessível**

1440x900, 1024x768, 768x1024, 390x844 e 360x800; teclado, foco, contraste, reduced
motion, ausência de overflow e valores essenciais sem hover.

- [ ] **Step 5: Scan de segurança e regressão**

Zero traceback, secret/account/path leak, tentativa REAL, duplicata de contrato,
posição órfã ou entrada acima de dois segundos. Diff protegido de Donchian,
Nexus Speed e indicadores permanece vazio.

- [ ] **Step 6: Atualizar documentação e decisão**

Registrar comandos, resultados, bugs RED/GREEN, screenshots sanitizados, instruções
VPS e condição para retomar teste REAL de USD 0,35.

- [ ] **Step 7: Commit final local**

Run: `git commit -m "test: validate NexusTrade live operations complement"`

## Completion Gate

- Usuário acompanha mercado, decisão, contrato e liquidação de Champion/Trial ao vivo.
- Gerenciamento do Champion é configurável, persistido, seguro e auditável.
- Trial, estratégia, ativo, timeframe e duração permanecem protegidos.
- Reconnect/restart reconstroem tela e configuração sem F5.
- Toda suíte e QA Docker/navegador passam com REAL bloqueado.
- Nenhum push é realizado.

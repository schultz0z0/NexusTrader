# Nexus Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar a estratégia Nexus Speed completa, com EMA(5), inclinação da EMA, ADX(10), ATR(14), confirmação tick a tick, contratos Rise/Fall com expiração fixa de 5 ticks, payout líquido mínimo de 87%, UI, replay e ambiente Docker local.

**Architecture:** Indicadores são calculados apenas em candles M1 fechados e congelados na abertura de cada vela. A estratégia mantém uma máquina de estados síncrona, processa ticks vivos por sequência monotônica e emite no máximo um sinal por vela; o BotSession valida payout, idade do sinal, risco e contrato antes da compra.

**Tech Stack:** Python 3.11, FastAPI, pandas, stock-indicators 1.3.5, .NET 8, WebSocket Deriv, JavaScript ES modules, Lightweight Charts, pytest, Node test runner, Docker Compose.

## Global Constraints

- `strategy_id` é `nexus_speed`; Donchian permanece compatível.
- Timeframe é 60 segundos; duração é fixa em 5 ticks (`duration=5`, `duration_unit="t"`). A Deriv rejeitou 10 segundos nos sintéticos durante o smoke demo e o usuário aprovou 5 ticks.
- Defaults fixos: EMA 5, ADX 10, ADX estritamente maior que 30, ATR 14, distância 0,30 ATR.
- EMA plana tolera até 1 pip; PUT aceita EMA plana/descendente e CALL aceita EMA plana/ascendente.
- Payout líquido mínimo é 0,87 e deve ser validado em toda proposal.
- Um único evento terminal por candle e nenhuma nova tentativa após confirmação, reprovação ou aborto.
- Nenhuma conta real será habilitada por esta implementação.
- Todos os testes, smoke tests e operações locais usarão exclusivamente `DERIV_ACCOUNT_TYPE=demo` e `ALLOW_REAL_TRADING=false`.
- É proibido selecionar, autorizar ou enviar ordens para uma conta real durante esta execução.

---

### Task 1: Dependência de indicadores e adaptação OHLC

**Files:**
- Create: `utils/indicator_quotes.py`
- Modify: `requirements.txt`
- Modify: `Dockerfile`
- Test: `tests/test_indicator_quotes.py`
- Test: `tests/test_deployment_contract.py`

**Interfaces:**
- Produces: `closed_candles(candles, active_candle_time) -> list[dict]`
- Produces: `candles_to_quotes(candles) -> list[Quote]`

- [ ] Escrever testes que exigem exclusão da vela ativa, UTC e OHLCV com volume zero.
- [ ] Executar `python -m pytest tests/test_indicator_quotes.py tests/test_deployment_contract.py -q` e confirmar falha pela ausência do módulo/dependência.
- [ ] Implementar o adaptador mínimo, fixar `stock-indicators==1.3.5` e disponibilizar .NET 8 no container.
- [ ] Reexecutar os testes focados e confirmar sucesso.
- [ ] Commitar `feat: add stock indicator runtime and quote adapter`.

### Task 2: Sequência monotônica de ticks vivos

**Files:**
- Modify: `data/market_data.py`
- Modify: `strategies/base.py`
- Test: `tests/test_market_data.py`
- Test: `tests/test_donchian_profile.py`

**Interfaces:**
- Produces tick: `{quote, epoch, symbol, sequence, pip_size, is_live}`
- Extends `Signal` with optional `tick_sequence: int | None` and `candle_time: int | None`.

- [ ] Escrever testes para sequência crescente, mesmo `epoch`, marcação histórica e compatibilidade do Signal existente.
- [ ] Executar os testes focados e confirmar as falhas esperadas por campos ausentes.
- [ ] Implementar contador por handler, normalização dos campos e defaults retrocompatíveis no Signal.
- [ ] Reexecutar os testes focados e toda a suíte Donchian.
- [ ] Commitar `feat: add deterministic live tick sequencing`.

### Task 3: Máquina de estados Nexus Speed

**Files:**
- Create: `strategies/nexus_speed.py`
- Test: `tests/test_nexus_speed.py`

**Interfaces:**
- Produces: `NexusSpeedStrategy(BaseStrategy)`
- Consumes: candles M1, ticks normalizados e configuração fixa.
- Emits: `Signal(action, reason, price, timestamp, tick_sequence, candle_time)`.

- [ ] Escrever fixtures determinísticas com indicadores injetáveis para testar estados sem depender do CLR.
- [ ] Escrever testes RED para warm-up, direção, ADX, ATR, distância e um sinal por vela.
- [ ] Implementar snapshot por candle e estados inelegíveis/armados mínimos.
- [ ] Escrever testes RED para inclinação: PUT descendente/plana aceito e ascendente rejeitado; CALL espelhado; tolerância exata de 1 pip.
- [ ] Implementar `ema_slope`, `flat_tolerance` e filtro congelado.
- [ ] Escrever testes RED para contato por interseção de banda, confirmação, tick plano, sequência quebrada, troca de vela e múltiplos ticks em lote.
- [ ] Implementar consumo sequencial dos ticks e estados `AWAITING_CONFIRMATION`, `SIGNAL_EMITTED` e `ABORTED`.
- [ ] Reexecutar `python -m pytest tests/test_nexus_speed.py -q` após cada ciclo e confirmar sucesso final.
- [ ] Commitar `feat: implement nexus speed state machine`.

### Task 4: Registro, contratos, payout, atraso e risco

**Files:**
- Modify: `core/bot_session.py`
- Modify: `trading/proposal.py`
- Modify: `core/connection.py` only if contract preflight needs a focused helper.
- Test: `tests/test_trade_lifecycle.py`
- Test: `tests/test_nexus_speed_runtime.py`

**Interfaces:**
- Produces: `ProposalManager.profit_ratio(proposal) -> float`.
- BotSession builds `nexus_speed`, validates `profit_ratio >= 0.87` and `latest_sequence - signal.tick_sequence <= 1`.

- [ ] Escrever testes RED para strategy factory e expiração fixa de 5 ticks.
- [ ] Implementar criação da estratégia a partir de `strategy_config`.
- [ ] Escrever testes RED para payout 86,99% bloqueado e 87% aceito.
- [ ] Implementar cálculo `(payout - ask_price) / ask_price` e bloqueio antes do buy.
- [ ] Escrever testes RED para sinal vencido por dois ticks, erro de proposal e nenhum retry no candle.
- [ ] Implementar validação de idade e eventos estruturados de bloqueio.
- [ ] Escrever e implementar preflight de disponibilidade Rise/Fall para símbolo/duração.
- [ ] Reexecutar os testes de lifecycle, ownership, risco e recuperação.
- [ ] Commitar `feat: enforce nexus speed execution gates`.

### Task 5: API, persistência e interface

**Files:**
- Modify: `api/routes/bots.py`
- Modify: `static/index.html`
- Modify: `static/js/bot_config.js`
- Modify: `static/js/app.js`
- Modify: `static/js/chart.js`
- Test: `tests/test_api.py`
- Test: `tests/test_frontend_contract.py`
- Test: `tests/js/bot_config.test.mjs`
- Test: `tests/js/chart.test.mjs`

**Interfaces:**
- API accepts `strategy_id=nexus_speed`, `duration=5`, `duration_unit="t"` and fixed strategy profile.
- UI serializes Nexus defaults and displays frozen EMA reference.

- [ ] Escrever testes RED para payload válido/inválido da Nexus e preservação do perfil Donchian.
- [ ] Implementar validação Pydantic discriminada por estratégia.
- [ ] Escrever testes JS RED para seleção, serialização de 5 ticks e defaults 1 pip/87%.
- [ ] Implementar campos e payload da UI.
- [ ] Escrever teste RED para série EMA criada/removida quando o contexto muda.
- [ ] Implementar plotagem da EMA congelada e legenda Nexus.
- [ ] Reexecutar testes Python e JS focados.
- [ ] Commitar `feat: expose nexus speed configuration and chart`.

### Task 6: Replay determinístico para contratos de 5 ticks

**Files:**
- Modify: `backtest/engine.py`
- Modify: `backtest/cli.py`
- Modify: `backtest/metrics.py` only for fields required by Nexus.
- Test: `tests/test_replay.py`
- Test: `tests/test_nexus_speed_replay.py`

**Interfaces:**
- Replay accepts equal epochs, assigns strict sequence and settles when `sequence >= entry_sequence + 5`.
- Production evaluation rejects trades without payout ratio.

- [ ] Escrever testes RED para epochs iguais, entrada no tick seguinte e expiração no quinto tick.
- [ ] Implementar ordenação por sequência e settlement por contagem de 5 ticks.
- [ ] Escrever teste RED para payout ausente resultar em trade inválido.
- [ ] Remover o default inventado de 0,8 na avaliação Nexus e preservar manifests determinísticos.
- [ ] Executar replay duas vezes e exigir hash, trades e métricas idênticos.
- [ ] Commitar `feat: add five-tick nexus replay`.

### Task 7: Observabilidade, regressão e Docker local completo

**Files:**
- Modify: `core/events.py` only if new payload fields require normalization.
- Modify: `docs/DEVELOPMENT.md`
- Modify: `docker-compose.yml` only if required to expose the complete local stack.
- Test: `tests/test_nexus_speed_runtime.py`
- Test: `tests/test_deployment_contract.py`

**Interfaces:**
- Emits qualification, touch, confirmation, payout, stale-signal and execution telemetry with candle/sequence.

- [ ] Escrever testes RED para códigos estruturados e métricas de latência/ticks.
- [ ] Implementar logs e eventos sem incluir credenciais.
- [ ] Executar a suíte Python completa com credenciais dummy de teste.
- [ ] Executar todos os testes JavaScript.
- [ ] Executar `docker compose config`.
- [ ] Executar `docker compose up --build -d` com o `.env` local autorizado.
- [ ] Verificar containers, health endpoints, página, API, banco, stream/eventos e logs sem segredos.
- [ ] Executar smoke test em conta demo sem habilitar conta real.
- [ ] Commitar `feat: complete nexus speed local stack`.

## Final verification

- [ ] `python -m pytest -q`: zero falhas.
- [ ] `node --test tests/js/*.test.mjs`: zero falhas.
- [ ] `docker compose config`: exit 0.
- [ ] `docker compose ps`: todos os serviços esperados ativos/healthy.
- [ ] HTTP da aplicação e API respondem com status esperado.
- [ ] `git status --short`: somente alterações intencionais ou clean após commits.
- [ ] Revisar a especificação `C:/Users/rapha/Downloads/Deriv/docs/planejamento_estrategia_ema_adx.md` requisito por requisito.

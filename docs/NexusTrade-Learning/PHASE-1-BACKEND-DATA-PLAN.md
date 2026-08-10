# NexusTrade Phase 1 — Backend and Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o NexusTrade completo no backend, operando Champion e Trial em lanes isoladas, coletando dados 24/7, treinando candidatos e expondo relatórios/promoções auditáveis por API.

**Architecture:** Um pacote novo `nexus_trade/` concentra domínio, estratégia, runtime, aprendizado e relatórios. Donchian e Nexus Speed continuam dentro do fluxo atual; apenas a factory do orquestrador aprende a rotear o singleton protegido para `NexusTradeRuntime`. SQLite guarda estado transacional e snapshots, enquanto ticks brutos são arquivados em segmentos gzip no volume persistente.

**Tech Stack:** Python 3.11, asyncio, FastAPI, aiosqlite, stock-indicators 1.3.5, pandas 3.0.5, scikit-learn 1.9.0, openpyxl 3.1.5, unittest, Docker Compose e Deriv WebSocket API.

## Global Constraints

- Donchian + ZigZag e Nexus Speed permanecem determinísticos; a suíte atual deve passar sem mudanças de expectativa.
- Existe exatamente um bot protegido com ID `nexus-trade`, nunca criável, duplicável ou excluível pelo usuário.
- `R_100`, M1 e expiração de 58 segundos são invariantes.
- Bollinger é obrigatória em todo Champion/Trial; Champion V1 usa Bollinger(20, 2, SMA) e ADX(14) com entrada permitida em `ADX <= 22`.
- Champion OFF e Trial usam a mesma conta DEMO, USD 0,35 e lanes separadas; Trial nunca usa REAL.
- Champion ON usa a conta selecionada e o gerenciamento configurado.
- Um contrato ativo no máximo por lane; sinais com atraso superior a 2 segundos são descartados.
- Toda promoção é humana, atômica, auditada, somente com Champion OFF e sem intent/contrato pendente.
- Relatórios fecham às 10:00 em `America/Sao_Paulo`; a semana começa segunda-feira às 10:00.
- Desenvolvimento e smoke tests compram somente em DEMO; `ALLOW_REAL_TRADING=false` durante validação.
- Nenhuma transição interna relevante pode ser silenciosa para API/WebSocket/frontend.

---

### Task 1: Baseline de regressão, dependências e configurações

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py`
- Create: `tests/test_nexus_trade_settings.py`
- Modify: `docs/DEVELOPMENT.md`

**Interfaces:**
- Produces: settings `NEXUS_TICK_ARCHIVE_PATH`, `NEXUS_DEMO_STAKE`, `NEXUS_DAILY_CLOSE_HOUR`, `NEXUS_ENTRY_MAX_DELAY_SECONDS`.

- [ ] **Step 1: Execute e salve o baseline sem alterar expectativas**

Run: `python -m unittest discover -s tests -v`

Expected: todos os testes atuais PASS; qualquer falha preexistente deve ser registrada antes de continuar.

- [ ] **Step 2: Escreva testes de defaults e travas**

```python
def test_nexus_defaults_are_safe(self):
    self.assertEqual(settings.NEXUS_DEMO_STAKE, 0.35)
    self.assertEqual(settings.NEXUS_DAILY_CLOSE_HOUR, 10)
    self.assertEqual(settings.NEXUS_ENTRY_MAX_DELAY_SECONDS, 2)
    self.assertEqual(settings.BUSINESS_TIMEZONE, "America/Sao_Paulo")
```

- [ ] **Step 3: Rode o teste e confirme a falha**

Run: `python -m unittest tests.test_nexus_trade_settings -v`

Expected: FAIL por atributos inexistentes.

- [ ] **Step 4: Adicione dependências e settings validados**

```text
scikit-learn==1.9.0
openpyxl==3.1.5
```

```python
NEXUS_TICK_ARCHIVE_PATH: str = "storage/nexus_ticks"
NEXUS_DEMO_STAKE: float = 0.35
NEXUS_DAILY_CLOSE_HOUR: int = 10
NEXUS_ENTRY_MAX_DELAY_SECONDS: int = 2
```

O validator deve rejeitar stake diferente de `0.35`, hora fora de `0..23` e atraso diferente de `2`.

- [ ] **Step 5: Rode settings, pip e regressão completa**

Run: `python -m unittest tests.test_nexus_trade_settings tests.test_settings -v`

Run: `python -m pip check`

Run: `python -m unittest discover -s tests -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config/settings.py tests/test_nexus_trade_settings.py docs/DEVELOPMENT.md
git commit -m "chore: prepare isolated NexusTrade runtime"
```

### Task 2: Schema, singleton e identidade das lanes

**Files:**
- Create: `nexus_trade/__init__.py`
- Create: `nexus_trade/constants.py`
- Create: `nexus_trade/domain.py`
- Create: `database/nexus_models.py`
- Create: `nexus_trade/repository.py`
- Modify: `database/repository.py`
- Create: `tests/test_nexus_trade_repository.py`

**Interfaces:**
- Produces: `NEXUS_TRADE_BOT_ID`, `Lane`, `CampaignStatus`, `VersionStatus`, `NexusTradeRepository`.
- Consumes later: todas as tasks usam IDs/hashes e métodos deste repositório.

- [ ] **Step 1: Escreva testes de provisionamento idempotente e unicidade**

```python
async def test_init_provisions_exactly_one_nexus_trade(self):
    await self.repo.init_db()
    await self.repo.init_db()
    bots = [b for b in await self.repo.list_bots() if b["strategy_id"] == "nexus_trade"]
    self.assertEqual([b["id"] for b in bots], ["nexus-trade"])
    self.assertEqual(bots[0]["symbol"], "R_100")
```

Inclua testes para duas lanes, Champion V1, campanha inicial e rejeição de segundo singleton.

- [ ] **Step 2: Rode e confirme a falha**

Run: `python -m unittest tests.test_nexus_trade_repository -v`

Expected: FAIL por schema/repositório inexistentes.

- [ ] **Step 3: Defina domínio imutável**

```python
NEXUS_TRADE_BOT_ID = "nexus-trade"
NEXUS_SYMBOL = "R_100"
NEXUS_TIMEFRAME_SECONDS = 60
NEXUS_DURATION_SECONDS = 58

class Lane(str, Enum):
    CHAMPION = "champion_baseline"
    TRIAL = "challenger_trial"
```

- [ ] **Step 4: Crie tabelas específicas**

`database/nexus_models.py` deve criar: `nexus_versions`, `nexus_runtime`,
`nexus_campaigns`, `nexus_candles`, `nexus_features`, `nexus_decisions`,
`nexus_candidates`, `nexus_reports`, `nexus_proposals`, `nexus_audit_events` e
`nexus_tick_segments`. Adicione `lane`, `nexus_version_id`, `campaign_id`, `decision_id`
e `entry_delay_ms` às migrations de `trades` e `order_intents`. Use índices únicos para
`(symbol, open_epoch)`, hashes de versão e uma campanha Trial ativa.

- [ ] **Step 5: Implemente provisionamento transacional**

```python
async def ensure_singleton(self) -> dict:
    """Create protected bot, Champion V1, runtime pointers and first campaign atomically."""

async def get_runtime_snapshot(self) -> dict:
    """Return singleton, lane pointers, versions and active campaign."""
```

Champion V1 persiste snapshot canônico com Bollinger 20/2/SMA, ADX14/22, R_100/M1/58s.

- [ ] **Step 6: Rode testes e regressão de repository**

Run: `python -m unittest tests.test_nexus_trade_repository tests.test_repository -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nexus_trade database tests/test_nexus_trade_repository.py
git commit -m "feat: persist NexusTrade singleton and versioned lanes"
```

### Task 3: Arquivo de ticks, candles fechados e features causais

**Files:**
- Create: `nexus_trade/tick_archive.py`
- Create: `nexus_trade/indicators.py`
- Create: `nexus_trade/features.py`
- Create: `tests/test_nexus_trade_tick_archive.py`
- Create: `tests/test_nexus_trade_features.py`

**Interfaces:**
- Produces: `TickArchive.append(tick)`, `TickArchive.replay(start,end)`, `IndicatorEngine.calculate(candles)`, `FeatureBuilder.build(...)`.

- [ ] **Step 1: Escreva testes de rotação diária, hash e replay ordenado**

```python
ticks = [{"epoch": 100, "quote": 1.0}, {"epoch": 101, "quote": 1.1}]
for tick in ticks:
    archive.append(tick)
self.assertEqual(list(archive.replay(100, 101)), ticks)
self.assertTrue(archive.close_segment()["sha256"])
```

- [ ] **Step 2: Escreva vetores determinísticos de Bollinger/ADX**

O teste compara período 20, desvio 2, SMA e ADX14 com valores produzidos diretamente
por `stock_indicators`, garante uso exclusivo de candles fechados e verifica warmup.

- [ ] **Step 3: Rode e confirme falha**

Run: `python -m unittest tests.test_nexus_trade_tick_archive tests.test_nexus_trade_features -v`

- [ ] **Step 4: Implemente arquivo append-only gzip e manifesto SQLite**

Segmentos ficam em `<NEXUS_TICK_ARCHIVE_PATH>/R_100/YYYY/MM/DD/*.jsonl.gz`, são fechados
atomicamente e registrados com início, fim, quantidade, bytes e SHA-256. Arquivo parcial
é recuperado no restart sem reordenar ou duplicar ticks.

- [ ] **Step 5: Implemente indicadores e repertório de features**

```python
@dataclass(frozen=True)
class IndicatorFrame:
    epoch: int
    upper: float
    middle: float
    lower: float
    adx: float
    values: dict[str, float | None]
```

Inclua %B, z-score, largura/inclinação Bollinger, CHOP, ATR/ATRP, RSI, Stochastic, CCI,
Keltner, ROC, Aroon, SMA/EMA/WMA/HMA/KAMA e características de corpo/pavios. Valores de
warmup permanecem `None`; não use preenchimento futuro.

- [ ] **Step 6: Rode testes**

Run: `python -m unittest tests.test_nexus_trade_tick_archive tests.test_nexus_trade_features -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nexus_trade/tick_archive.py nexus_trade/indicators.py nexus_trade/features.py tests
git commit -m "feat: archive R_100 ticks and build causal features"
```

### Task 4: Estratégia Champion V1 e relógio de entrada M1

**Files:**
- Create: `nexus_trade/strategy.py`
- Create: `nexus_trade/clock.py`
- Create: `tests/test_nexus_trade_strategy.py`
- Create: `tests/test_nexus_trade_clock.py`

**Interfaces:**
- Produces: `NexusTradeStrategy.on_closed_candle(candle, indicators) -> list[Decision]`, `EntryClock.schedule(decision) -> EntryIntent`.

- [ ] **Step 1: Escreva testes de todas as regras aprovadas**

```python
decision = strategy.on_closed_candle(bearish_confirmation, frame(adx=22.0))
self.assertEqual(decision[0].contract_type, "PUT")
self.assertEqual(decision[0].target_epoch, bearish_confirmation.open_epoch + 60)
```

Cubra rompimento superior/inferior por fechamento, inclinação da banda, espera ilimitada,
doji, igualdade, cruzamento corporal da central, ADX `21.99/22/22.01`, deduplicação de
motivos e consumo do sinal bloqueado.

- [ ] **Step 2: Escreva testes de latência**

Teste despacho no boundary, classificação `TARGET`, `CONTINGENCY` e `STALE` para atrasos
de 250 ms, 1 s, 2 s e 2001 ms.

- [ ] **Step 3: Rode e confirme falha**

Run: `python -m unittest tests.test_nexus_trade_strategy tests.test_nexus_trade_clock -v`

- [ ] **Step 4: Implemente estado puro e serializável**

```python
@dataclass
class SetupState:
    upper_break_epoch: int | None = None
    lower_break_epoch: int | None = None

@dataclass(frozen=True)
class Decision:
    decision_id: str
    contract_type: str | None
    reason_codes: tuple[str, ...]
    signal_epoch: int
    target_epoch: int
    adx: float
    blocked_reason: str | None
```

Persistir toda decisão, inclusive `NO_TRADE`, `ADX_BLOCKED`, `POSITION_ACTIVE` e `STALE`.

- [ ] **Step 5: Implemente relógio monotônico alinhado ao epoch Deriv**

O relógio finaliza o bucket anterior na virada, agenda a compra sem esperar o primeiro
tick novo e registra `dispatch_epoch`, `accepted_epoch` e `entry_delay_ms`.

- [ ] **Step 6: Rode testes e replay determinístico**

Run: `python -m unittest tests.test_nexus_trade_strategy tests.test_nexus_trade_clock tests.test_candles -v`

- [ ] **Step 7: Commit**

```bash
git add nexus_trade/strategy.py nexus_trade/clock.py tests
git commit -m "feat: implement NexusTrade V1 state machine"
```

### Task 5: Runtime contínuo, duas lanes e dispatcher compartilhado

**Files:**
- Create: `nexus_trade/dispatcher.py`
- Create: `nexus_trade/runtime.py`
- Create: `tests/test_nexus_trade_dispatcher.py`
- Create: `tests/test_nexus_trade_runtime.py`
- Modify: `core/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

**Interfaces:**
- Produces: `SharedDemoDispatcher.submit(intent)`, `NexusTradeRuntime.run()`, `NexusTradeRuntime.apply_champion_mode(snapshot)`.
- Consumes: strategy decisions, repository lanes, existing `NexusConnection`, `ProposalManager`, `OrderOwnershipCoordinator`, `ContractMonitor`.

- [ ] **Step 1: Teste serialização de buys e posição por lane**

```python
first, second = await asyncio.gather(
    dispatcher.submit(champion_intent),
    dispatcher.submit(trial_intent),
)
self.assertNotEqual(first.contract_id, second.contract_id)
self.assertEqual(fake_connection.max_parallel_buy_calls, 1)
```

Teste que cada lane pode manter um contrato, a terceira entrada é bloqueada, Trial nunca
recebe conta REAL e Champion OFF sempre usa DEMO/0,35.

- [ ] **Step 2: Teste semântica contínua do orquestrador**

`nexus-trade` deve possuir runtime mesmo com `desired_state=STOPPED`; bots comuns
continuam iniciando/parando exatamente como antes.

- [ ] **Step 3: Rode e confirme falha**

Run: `python -m unittest tests.test_nexus_trade_dispatcher tests.test_nexus_trade_runtime tests.test_orchestrator -v`

- [ ] **Step 4: Implemente dispatcher e runtime**

O runtime mantém um stream R_100/M1, dois estados de estratégia e conexões por conta.
Champion OFF e Trial compartilham dispatcher DEMO; Champion ON/REAL usa executor separado,
sem transferir o Trial. `emergency_stop` interrompe novas compras de ambas as lanes.

- [ ] **Step 5: Estenda somente a factory/reconcile do orquestrador**

```python
def _default_session_factory(self, bot):
    if bot.get("strategy_id") == "nexus_trade":
        return NexusTradeRuntime(self.repository, bot)
    return BotSession(self.repository, bot)
```

Não altere `strategies/donchian_zigzag.py`, `strategies/nexus_speed.py` ou suas expectativas.

- [ ] **Step 6: Rode regressão crítica**

Run: `python -m unittest tests.test_nexus_trade_runtime tests.test_orchestrator tests.test_donchian_profile tests.test_nexus_speed tests.test_nexus_speed_runtime -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nexus_trade/dispatcher.py nexus_trade/runtime.py core/orchestrator.py tests
git commit -m "feat: run isolated continuous NexusTrade lanes"
```

### Task 6: API protegida, eventos e snapshots

**Files:**
- Create: `api/routes/nexus_trade.py`
- Modify: `api/app.py`
- Modify: `api/routes/bots.py`
- Modify: `api/live_store.py`
- Create: `tests/test_nexus_trade_api.py`
- Modify: `tests/test_control_plane.py`

**Interfaces:**
- Produces endpoints `/api/v1/nexus-trade`, `/mode`, `/emergency-stop`, `/versions`, `/campaigns`, `/reports`, `/proposals`, `/exports` e eventos `nexus.*`.

- [ ] **Step 1: Escreva testes de singleton protegido**

```python
response = self.client.post("/api/v1/bots", json={**payload, "strategy_id": "nexus_trade"})
self.assertEqual(response.status_code, 422)
self.assertEqual(self.client.delete("/api/v1/bots/nexus-trade").status_code, 409)
self.assertNotIn("nexus_trade", {s["id"] for s in self.client.get("/api/v1/strategies").json()["data"]})
```

- [ ] **Step 2: Escreva testes de modo e WebSocket snapshot**

Garanta OFF/DEMO, ON/DEMO, ON/REAL, parada total, versão/campanha e lanes no snapshot;
REAL continua sujeito aos tickets e flags atuais.

- [ ] **Step 3: Rode e confirme falha**

Run: `python -m unittest tests.test_nexus_trade_api tests.test_control_plane -v`

- [ ] **Step 4: Implemente schemas e rotas**

```python
class ChampionModePayload(BaseModel):
    enabled: bool
    account_id: str
    account_type: Literal["demo", "real"]
    real_ticket: str = ""
```

Rotas chamam serviços/repositórios; não contêm estratégia ou treino.

- [ ] **Step 5: Publique snapshots/eventos idempotentes**

Inclua `nexus.runtime`, `nexus.decision`, `nexus.trade`, `nexus.campaign`,
`nexus.report`, `nexus.trial_changed`, `nexus.proposal` e `nexus.version_changed`.

- [ ] **Step 6: Rode API e regressão**

Run: `python -m unittest tests.test_nexus_trade_api tests.test_control_plane tests.test_api -v`

- [ ] **Step 7: Commit**

```bash
git add api nexus_trade tests
git commit -m "feat: expose protected NexusTrade control plane"
```

### Task 7: Dataset, treinamento e candidatos em sombra

**Files:**
- Create: `nexus_trade/dataset.py`
- Create: `nexus_trade/training.py`
- Create: `nexus_trade/candidates.py`
- Create: `nexus_trade/artifacts.py`
- Create: `tests/test_nexus_trade_learning.py`

**Interfaces:**
- Produces: `DatasetBuilder.build(cutoff_epoch)`, `Trainer.fit(dataset, trial_ledger)`, `CandidateRegistry.register(artifact)`.

- [ ] **Step 1: Teste corte temporal e ausência de leakage**

```python
dataset = builder.build(cutoff_epoch=10_000)
self.assertTrue((dataset.train.label_epoch < dataset.validation.feature_epoch.min()).all())
self.assertTrue((dataset.validation.label_epoch < dataset.test.feature_epoch.min()).all())
```

- [ ] **Step 2: Teste reprodutibilidade do artefato**

Mesmo dataset hash, configuração e seed devem produzir o mesmo schema, métricas e hash
de metadados; tentativa ruim também entra no ledger.

- [ ] **Step 3: Rode e confirme falha**

Run: `python -m unittest tests.test_nexus_trade_learning -v`

- [ ] **Step 4: Implemente modelo inicial interpretável**

Use `HistGradientBoostingClassifier(random_state=...)` para estimar probabilidade de win
dos setups Bollinger. O modelo decide `OPERAR/NÃO OPERAR`; direção continua vindo da
estratégia Bollinger. O limiar deriva do payout de equilíbrio mais margem versionada.

- [ ] **Step 5: Implemente walk-forward, ledger e ablação**

Treino, validação e teste são cronológicos. Cada candidato registra indicadores,
parâmetros, features, seed, número total de tentativas, ablação e métricas. Apenas um
candidato congelado vira Trial; os demais ficam `SHADOW`.

- [ ] **Step 6: Rode testes**

Run: `python -m unittest tests.test_nexus_trade_learning -v`

- [ ] **Step 7: Commit**

```bash
git add nexus_trade/dataset.py nexus_trade/training.py nexus_trade/candidates.py nexus_trade/artifacts.py tests/test_nexus_trade_learning.py
git commit -m "feat: train versioned NexusTrade shadow candidates"
```

### Task 8: Relatórios, gates, campanha e exports

**Files:**
- Create: `nexus_trade/metrics.py`
- Create: `nexus_trade/gates.py`
- Create: `nexus_trade/reports.py`
- Create: `nexus_trade/scheduler.py`
- Create: `nexus_trade/exports.py`
- Create: `tests/test_nexus_trade_reports.py`
- Create: `tests/test_nexus_trade_gates.py`
- Create: `tests/test_nexus_trade_exports.py`

**Interfaces:**
- Produces: `calculate_lane_metrics`, `PromotionGateEvaluator.evaluate`, `ReportService.close_daily/close_weekly`, `ReportExporter.csv_zip/xlsx`.

- [ ] **Step 1: Teste métricas e assertividade**

```python
metrics = calculate_lane_metrics([won, lost, tie])
self.assertEqual(metrics.n_total, 3)
self.assertEqual(metrics.n_decisive, 2)
self.assertEqual(metrics.accuracy, 0.5)
self.assertEqual(metrics.capital_at_risk, 1.05)
```

Teste expectancy por stake, payout, profit factor, drawdown, recovery, pior bloco 50,
sequência de losses e agregação por soma — nunca média simples de percentuais.

- [ ] **Step 2: Teste calendário Brasília e continuidade**

Fechamento diário 10:00, segunda 10:00, restart atrasado preservando janela, 300
acumulado entre semanas, Trial B reiniciando 0/300 e campanha anterior `SUPERSEDED`.

- [ ] **Step 3: Teste todos os gates aprovados**

Cubra `PASSOU/FALHOU/INCONCLUSIVO`, +2 p.p. de expectancy, PF 1,10, bootstrap 95%,
drawdown, estabilidade, DSR/PBO, integridade e orçamento de uma família de mudança.

- [ ] **Step 4: Implemente snapshots imutáveis**

```python
@dataclass(frozen=True)
class GateResult:
    code: str
    status: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    observed: float | str | None
    threshold: float | str
    reason: str
```

Relatórios carregam versões, hashes, dados diários, semana completa, acumulado, diffs,
recomendação e todos os gates.

- [ ] **Step 5: Implemente CSV ZIP e XLSX**

CSV contém summary/days/champion/trial/indicators/gates/audit. XLSX contém abas
Summary, Days, Champion, Trial, Indicators, Gates e Audit, sempre geradas do snapshot.

- [ ] **Step 6: Rode testes**

Run: `python -m unittest tests.test_nexus_trade_reports tests.test_nexus_trade_gates tests.test_nexus_trade_exports -v`

- [ ] **Step 7: Commit**

```bash
git add nexus_trade tests
git commit -m "feat: produce governed NexusTrade reports and exports"
```

### Task 9: Promoção, reanálise, Trial automático e rollback

**Files:**
- Create: `nexus_trade/promotion.py`
- Modify: `api/routes/nexus_trade.py`
- Create: `tests/test_nexus_trade_promotion.py`

**Interfaces:**
- Produces: `PromotionService.approve`, `reanalyze`, `replace_trial`, `rollback`.

- [ ] **Step 1: Escreva testes transacionais**

Teste bloqueio quando Champion ON/intent/contrato ativo, gates duros inválidos ou hash
incompatível; teste promoção atômica, auditoria, WebSocket, rollback e falha restaurando
Champion anterior.

- [ ] **Step 2: Teste troca automática semanal**

Com sombra adequado: Trial A vira `SUPERSEDED`, Trial B recebe novo ID/hash e 0/300. Sem
candidato: A continua. Proposta humana pendente congela troca.

- [ ] **Step 3: Rode e confirme falha**

Run: `python -m unittest tests.test_nexus_trade_promotion -v`

- [ ] **Step 4: Implemente CAS de revisão e audit log**

```python
async def approve(self, proposal_id: str, expected_revision: int, actor: str) -> dict:
    """Promote atomically only if revision, lane safety and immutable gates still match."""
```

Toda ação persiste before/after, actor, motivo, timestamps, hashes e resultado.

- [ ] **Step 5: Rode testes de promoção e API**

Run: `python -m unittest tests.test_nexus_trade_promotion tests.test_nexus_trade_api -v`

- [ ] **Step 6: Commit**

```bash
git add nexus_trade/promotion.py api/routes/nexus_trade.py tests
git commit -m "feat: govern NexusTrade promotion and rollback"
```

### Task 10: Verificação integral local, Docker e DEMO

**Files:**
- Modify: `scripts/start_dev.ps1`
- Create: `scripts/nexus_trade_smoke.py`
- Modify: `docs/DEVELOPMENT.md`
- Create: `docs/NexusTrade-Learning/PHASE-1-VALIDATION.md`

**Interfaces:**
- Produces: evidência repetível de readiness, runtime contínuo, persistência e contrato API.

- [ ] **Step 1: Rode validação estática e suíte completa**

Run: `python -m compileall -q api backtest core data database nexus_trade risk strategies trading`

Run: `python -m unittest discover -s tests -v`

Run: `node --test tests/js/*.test.mjs`

Run: `python -m pip check`

Expected: PASS, inclusive toda suíte Donchian/Nexus Speed.

- [ ] **Step 2: Suba localhost seguro**

Run: `powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1`

Expected: API em `http://127.0.0.1:8990`, banco dev isolado, `ALLOW_REAL_TRADING=false`.

- [ ] **Step 3: Execute smoke somente leitura e smoke DEMO controlado**

Run: `python -m scripts.smoke_deriv`

Run: `python -m scripts.nexus_trade_smoke --base-url http://127.0.0.1:8990 --demo-only`

O segundo comando configura explicitamente a conta DEMO, observa pelo menos uma virada
M1 e só compra USD 0,35 se surgir sinal aprovado. Nunca força sinal nem usa REAL.

- [ ] **Step 4: Verifique restart e logs**

Reinicie API e bot separadamente, confirme mesmo Champion/Trial/campanha/contadores,
nenhuma ordem duplicada e reconciliação de contratos. Inspecione logs por `ERROR`,
`ambiguous`, `duplicate`, atrasos >2s e falhas de hash.

- [ ] **Step 5: Rode Docker local**

Run: `docker compose config`

Run: `docker compose up --build`

Expected: ambos os serviços healthy, volume persistente, API localhost configurada e
nenhuma tentativa REAL.

- [ ] **Step 6: Registre evidências**

`PHASE-1-VALIDATION.md` recebe comandos, data, duração, hashes, contagens, versão,
resultado dos testes, incidentes e decisão `GO/NO-GO` para a Fase 2; nunca recebe tokens.

- [ ] **Step 7: Commit**

```bash
git add scripts docs/DEVELOPMENT.md docs/NexusTrade-Learning/PHASE-1-VALIDATION.md
git commit -m "test: validate NexusTrade backend in local demo stack"
```

## Phase 1 completion gate

- Suíte Python/JS, compileall e pip check passam.
- Donchian e Nexus Speed mantêm todos os testes existentes.
- Singleton e runtime sobrevivem a restart sem duplicidade.
- Champion OFF e Trial executam contratos DEMO separados de USD 0,35.
- Relatórios, gates, promoção, rollback e exports são reproduzíveis via API.
- Logs e snapshots comprovam transições em tempo real.
- Nenhuma compra REAL ocorreu.

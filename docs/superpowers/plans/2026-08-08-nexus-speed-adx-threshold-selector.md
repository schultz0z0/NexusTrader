# Nexus Speed ADX Threshold Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar ao Nexus Speed um seletor persistido de ADX mínimo com opções 20, 25 e 30, mantendo 30 como padrão e aplicando o valor salvo no runtime e no replay.

**Architecture:** `strategy_config.adx_threshold` continua sendo a fonte única do valor por bot. A UI monta o perfil com o valor selecionado, a API normaliza configurações antigas e rejeita valores fora da whitelist, e o runtime injeta o limiar validado na estratégia quando a sessão inicia. O replay instancia a mesma estratégia com o limiar escolhido e registra o valor no manifesto.

**Tech Stack:** Python 3.11, FastAPI/Pydantic, unittest/pytest, JavaScript ES modules, Node test runner, Docker Compose.

## Global Constraints

- Valores aceitos: exatamente os inteiros 20, 25 e 30.
- Valor padrão: 30.
- A regra permanece estrita: o candle exige `ADX > adx_threshold`.
- EMA(5), ADX(10), ATR(14), distância de 0,30 ATR, retorno mínimo de 87% e expiração de 5 ticks permanecem fixos.
- A alteração salva só é aplicada na próxima inicialização do bot.
- Trading real continua bloqueado no ambiente local; testes ao vivo usam somente demo.

---

### Task 1: Validação, estratégia e runtime

**Files:**
- Modify: `tests/test_nexus_speed.py`
- Modify: `tests/test_nexus_speed_control_plane.py`
- Modify: `tests/test_nexus_speed_runtime.py`
- Modify: `strategies/nexus_speed.py`
- Modify: `api/routes/bots.py`
- Modify: `core/bot_session.py`

**Interfaces:**
- Consumes: `BotPayload.strategy_config: dict`.
- Produces: `NexusSpeedStrategy(adx_threshold: int = 30)` com `adx_threshold: float`; `BotSession._build_strategy()` injeta o valor persistido.

- [ ] **Step 1: Escrever os testes RED do domínio**

Adicionar casos que provem: ADX igual ao limiar reprova, ADX acima aprova, 20/25/30 são aceitos, 21 é rejeitado e o runtime recebe 25.

```python
def test_custom_adx_threshold_is_strict(self):
    strategy, _ = self._armed_strategy(
        self._snapshot(adx=25.0), opening=110.0, adx_threshold=25
    )
    self.assertEqual(strategy.state_reason, "adx_below_threshold")

def test_runtime_uses_persisted_adx_threshold(self):
    strategy = self._session(
        strategy_config={"adx_threshold": 25}
    )._build_strategy()
    self.assertEqual(strategy.adx_threshold, 25.0)
```

- [ ] **Step 2: Executar os testes e confirmar RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest -q tests/test_nexus_speed.py tests/test_nexus_speed_control_plane.py tests/test_nexus_speed_runtime.py`

Expected: FAIL porque o construtor ainda não aceita `adx_threshold`, a API ainda exige 30 e o runtime não repassa 25.

- [ ] **Step 3: Implementar a whitelist e a injeção mínima**

```python
ALLOWED_ADX_THRESHOLDS = frozenset({20, 25, 30})

def __init__(..., adx_threshold: int = 30, ...):
    if type(adx_threshold) is not int or adx_threshold not in ALLOWED_ADX_THRESHOLDS:
        raise ValueError("Nexus Speed aceita ADX mínimo 20, 25 ou 30")
    self.adx_threshold = float(adx_threshold)
```

Na API, montar o perfil fixo com o limiar solicitado ou 30 quando ausente, validar somente a whitelist e continuar rejeitando qualquer deriva nos demais campos. Em `BotSession._build_strategy()`, passar `adx_threshold=int(strategy_config.get("adx_threshold", 30))`.

- [ ] **Step 4: Executar os testes e confirmar GREEN**

Run: `rtk .\.venv\Scripts\python.exe -m pytest -q tests/test_nexus_speed.py tests/test_nexus_speed_control_plane.py tests/test_nexus_speed_runtime.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add tests/test_nexus_speed.py tests/test_nexus_speed_control_plane.py tests/test_nexus_speed_runtime.py strategies/nexus_speed.py api/routes/bots.py core/bot_session.py
rtk git commit -m "feat: make nexus adx threshold configurable"
```

### Task 2: Formulário e payload do frontend

**Files:**
- Modify: `tests/js/bot_config.test.mjs`
- Modify: `static/js/bot_config.js`
- Modify: `static/js/app.js`
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `<select name="adx_threshold">` e `bot.strategy_config.adx_threshold`.
- Produces: `strategyProfile(strategyId: string, overrides?: object)` e `configuredBotPayload()` preservando o limiar salvo.

- [ ] **Step 1: Escrever os testes RED do perfil JavaScript**

```javascript
test("Nexus defaults to ADX 30", () => {
  assert.equal(strategyProfile("nexus_speed").strategy_config.adx_threshold, 30);
});

test("Nexus preserves selected ADX threshold", () => {
  const profile = strategyProfile("nexus_speed", { adx_threshold: 25 });
  assert.equal(profile.strategy_config.adx_threshold, 25);
});
```

Também testar que `configuredBotPayload()` mantém 20 ao trocar a conta global.

- [ ] **Step 2: Executar os testes e confirmar RED**

Run: `rtk node --test tests/js/bot_config.test.mjs`

Expected: FAIL porque `strategyProfile()` ignora overrides e `configuredBotPayload()` força 30.

- [ ] **Step 3: Implementar o seletor e o payload mínimo**

```javascript
export function strategyProfile(strategyId, overrides = {}) {
  const adxThreshold = Number(overrides.adx_threshold ?? 30);
  return {
    strategy_id: "nexus_speed",
    strategy_config: { ...NEXUS_SPEED_CONFIG, adx_threshold: adxThreshold },
    timeframe_seconds: 60,
    duration: 5,
    duration_unit: "t",
  };
}
```

No formulário Nexus, substituir o filtro fixo por `<select name="adx_threshold"><option>20</option><option>25</option><option selected>30</option></select>` e manter a distância de 0,30 ATR visível em campo separado. `formPayload()` passa o valor selecionado; `configuredBotPayload()` passa a configuração persistida.

- [ ] **Step 4: Executar os testes e confirmar GREEN**

Run: `rtk node --test tests/js/bot_config.test.mjs`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add tests/js/bot_config.test.mjs static/js/bot_config.js static/js/app.js static/index.html
rtk git commit -m "feat: add nexus adx selector to bot form"
```

### Task 3: Replay determinístico e manifesto

**Files:**
- Modify: `tests/test_replay.py`
- Modify: `backtest/cli.py`
- Modify: `backtest/engine.py`

**Interfaces:**
- Consumes: `strategy_factory(strategy_id: str, adx_threshold: int = 30)`.
- Produces: factory Nexus configurada e `manifest["adx_threshold"]`.

- [ ] **Step 1: Escrever o teste RED do replay**

```python
def test_replay_manifest_records_selected_adx_threshold(self):
    factory = strategy_factory("nexus_speed", adx_threshold=25)
    strategy = factory()
    self.assertEqual(strategy.adx_threshold, 25.0)
```

No teste de replay Nexus, verificar também `result["manifest"]["adx_threshold"] == 25.0` usando uma factory de cinco ticks que exponha esse atributo.

- [ ] **Step 2: Executar o teste e confirmar RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest -q tests/test_replay.py`

Expected: FAIL porque a factory não aceita o argumento e o manifesto não registra o limiar.

- [ ] **Step 3: Implementar factory, CLI e manifesto**

Adicionar `--adx-threshold` com `choices=(20, 25, 30)` e `default=30`. Para Nexus, retornar uma factory que constrói `NexusSpeedStrategy(adx_threshold=adx_threshold)`. No manifesto Nexus, adicionar:

```python
manifest["adx_threshold"] = float(getattr(strategy, "adx_threshold", 30.0))
```

- [ ] **Step 4: Executar o teste e confirmar GREEN**

Run: `rtk .\.venv\Scripts\python.exe -m pytest -q tests/test_replay.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add tests/test_replay.py backtest/cli.py backtest/engine.py
rtk git commit -m "feat: carry nexus adx threshold into replay"
```

### Task 4: Documentação, regressão e Docker demo

**Files:**
- Modify: `docs/superpowers/plans/2026-08-08-nexus-speed-implementation.md`
- Modify: `C:/Users/rapha/Downloads/Deriv/docs/planejamento_estrategia_ema_adx.md`

**Interfaces:**
- Consumes: comportamento implementado nas Tasks 1–3.
- Produces: documentação final coerente e ambiente local validado.

- [ ] **Step 1: Atualizar a documentação**

Substituir “ADX fixo em 30” por “ADX mínimo selecionável em 20, 25 ou 30; padrão 30; regra estrita `ADX > limiar`” e registrar que o valor salvo só é aplicado após iniciar o bot.

- [ ] **Step 2: Executar a regressão completa**

Run: `rtk .\.venv\Scripts\python.exe -m pytest -q`

Expected: 100% PASS.

Run: `rtk node --test tests/js/*.test.mjs`

Expected: 100% PASS.

- [ ] **Step 3: Reconstruir o Docker mantendo demo**

Executar `docker compose up -d --build --force-recreate` com `DERIV_ACCOUNT_TYPE=demo` e `ALLOW_REAL_TRADING=false`. Confirmar API ready, bot container ativo e ambas as variáveis efetivas sem imprimir segredos.

- [ ] **Step 4: Validar a interface**

No navegador local, criar/editar Nexus, verificar padrão 30, selecionar 25, salvar, reabrir e confirmar 25 persistido. Restaurar 30 no bot atualmente usado se a validação não tiver autorização para mudar sua estratégia operacional.

- [ ] **Step 5: Commit final**

```bash
rtk git add docs/superpowers/plans/2026-08-08-nexus-speed-implementation.md
rtk git commit -m "docs: document nexus adx threshold options"
```

- [ ] **Step 6: Verificação da árvore**

Run: `rtk git diff --check`

Expected: sem saída.

Run: `rtk git status --short`

Expected: sem alterações rastreadas pendentes; o plano externo atualizado permanece fora deste repositório.

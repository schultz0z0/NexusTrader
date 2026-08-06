# Local Dev Stack Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Criar um perfil localhost reproduzível para API e bot conectados somente à conta Deriv demo, preservando integralmente Donchian+ZigZag em candles de 1 minuto e contratos de 2 minutos.

**Architecture:** Um launcher PowerShell aplica overrides seguros sobre o `.env` existente, inicia API e bot com SQLite local isolado e bloqueia conta real. Antes disso, os defaults órfãos da Bollinger são alinhados ao único runtime suportado para que um banco novo nunca gere uma sessão incompatível.

**Tech Stack:** PowerShell, Python 3.12 local/3.11 Docker, FastAPI, SQLite, pytest, Node.js test runner.

---

### Task 1: Fixar o contrato do único perfil de estratégia suportado

**Files:**
- Modify: `tests/test_repository.py`
- Modify: `tests/test_control_plane.py`
- Create: `tests/test_donchian_profile.py`
- Modify: `api/routes/bots.py`
- Modify: `database/repository.py`
- Modify: `database/models.py`
- Modify: `static/index.html`

**Step 1: Escrever testes que falham para os defaults atuais**

Adicionar testes que inicializam um banco vazio e exigem:

```python
self.assertEqual(bot["strategy_id"], "donchian")
self.assertEqual(bot["symbol"], "R_75")
self.assertEqual(bot["timeframe_seconds"], 60)
self.assertEqual(bot["duration"], 2)
self.assertEqual(bot["duration_unit"], "m")
```

No schema HTTP, exigir que `strategy_id="bollinger"` resulte em 422 e que payload sem estratégia nasça como `donchian`.

**Step 2: Rodar os testes e confirmar a regressão**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_repository.py tests/test_control_plane.py tests/test_donchian_profile.py -q
```

Expected: FAIL mostrando defaults `bollinger`, `R_100`, 5 ticks.

**Step 3: Alinhar somente os defaults e a validação**

- `BotPayload.strategy_id = "donchian"`.
- Rejeitar qualquer `strategy_id` diferente de `donchian`.
- Default bot: nome `Donchian`, `strategy_id=donchian`, `symbol=R_75`, timeframe `60`, duração `2`, unidade `m`.
- Remover a opção Bollinger do HTML e deixar Donchian selecionado.
- Não alterar `strategies/donchian_zigzag.py` nem `utils/indicators.py`.

**Step 4: Rodar os testes focados**

Run: comando do Step 2.

Expected: PASS.

**Step 5: Commit**

```powershell
git add api/routes/bots.py database/repository.py database/models.py static/index.html tests/test_repository.py tests/test_control_plane.py tests/test_donchian_profile.py
git commit -m "fix: align defaults with donchian runtime"
```

### Task 2: Remover testes órfãos sem apagar cobertura útil

**Files:**
- Delete: `tests/test_bollinger.py`
- Delete: `test_stock_indicators.py`
- Modify: `test_zigzag.py`
- Test: `tests/test_donchian_profile.py`

**Step 1: Cobrir a implementação real do ZigZag**

Mover os cenários relevantes de `test_zigzag.py` para `tests/test_donchian_profile.py`, importando:

```python
from utils.indicators import calculate_zigzag
from strategies.donchian_zigzag import DonchianZigZagStrategy
```

Verificar depth 15, deviation 0, backstep 3 e duração 2 minutos sem alterar seus valores.

**Step 2: Rodar a suíte completa e confirmar os dois erros de coleta antigos**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected antes da remoção: erros de importação para `stock_indicators` e `strategies.bollinger`.

**Step 3: Remover somente os arquivos órfãos**

Excluir os testes de uma biblioteca não declarada e da estratégia já removida. Preservar os cenários ZigZag no teste novo.

**Step 4: Rodar a suíte completa**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/js/chart.test.mjs tests/js/market_context.test.mjs
```

Expected: todos os testes passam sem exclusões manuais.

**Step 5: Commit**

```powershell
git add tests/test_donchian_profile.py tests/test_bollinger.py test_stock_indicators.py test_zigzag.py
git commit -m "test: replace removed strategy coverage"
```

### Task 3: Criar launcher seguro da stack local

**Files:**
- Create: `scripts/start_dev.ps1`
- Create: `tests/test_dev_profile.py`

**Step 1: Escrever teste de contrato do launcher**

O teste deve ler `scripts/start_dev.ps1` e exigir:

```python
self.assertIn('$env:ALLOW_REAL_TRADING = "false"', script)
self.assertIn('$env:DOMAIN = "localhost"', script)
self.assertIn('nexus_trader.dev.db', script)
self.assertIn('127.0.0.1', script)
self.assertNotIn('ALLOW_REAL_TRADING = "true"', script)
```

**Step 2: Rodar e confirmar falha**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dev_profile.py -q
```

Expected: FAIL porque o launcher ainda não existe.

**Step 3: Implementar o launcher**

O script deve:

- validar `.venv\Scripts\python.exe` e `.env`;
- definir `DOMAIN=localhost`, `ALLOW_REAL_TRADING=false`, `DB_PATH=storage/nexus_trader.dev.db` e `API_BASE_URL=http://127.0.0.1:8990`;
- criar `storage/` e `logs/`;
- iniciar Uvicorn em loopback;
- aguardar `/api/v1/health`;
- iniciar `main.py` somente após a API ficar saudável;
- imprimir PIDs e caminhos dos logs;
- em Ctrl+C, encerrar apenas os dois PIDs iniciados pelo script.

Não deve editar o `.env`, escolher conta real nem alterar bot/configuração automaticamente.

**Step 4: Rodar o teste e smoke de API**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dev_profile.py -q
powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1
curl.exe -fsS http://127.0.0.1:8990/api/v1/health
```

Expected: teste PASS e health `{"status":"ok","database":"ok"}`.

**Step 5: Commit**

```powershell
git add scripts/start_dev.ps1 tests/test_dev_profile.py
git commit -m "dev: add safe local stack launcher"
```

### Task 4: Configurar o bot local exclusivamente em demo

**Files:**
- Modify: `docs/DEVELOPMENT.md`
- Test: `tests/test_dev_profile.py`

**Step 1: Documentar o procedimento sem armazenar segredos**

Adicionar ao guia:

1. executar `scripts/start_dev.ps1`;
2. abrir `http://127.0.0.1:8990`;
3. informar a chave local já presente no `.env`;
4. selecionar a conta marcada `DEMO`;
5. confirmar `R_75`, `1m`, Donchian+ZigZag e expiração 2m;
6. iniciar um único bot e acompanhar uma operação demo completa;
7. parar o bot e só então encerrar o launcher.

Incluir aviso explícito de que o perfil bloqueia conta real no backend.

**Step 2: Validar o contrato documental**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dev_profile.py -q
```

Expected: PASS.

**Step 3: Smoke integrado em demo**

Confirmar no painel e logs:

- histórico inicial de 500 candles;
- ticks atualizando o candle de 1 minuto;
- Donchian e ZigZag visíveis;
- `trade.opened` → `trade.updated` → `trade.closed`;
- SQLite com trade `closed`;
- parada chegando a `STOPPED`.

**Step 4: Commit**

```powershell
git add docs/DEVELOPMENT.md tests/test_dev_profile.py
git commit -m "docs: add localhost demo workflow"
```

### Task 5: Verificação final sem mudar a estratégia

**Files:**
- Verify only: `strategies/donchian_zigzag.py`
- Verify only: `utils/indicators.py`

**Step 1: Confirmar ausência de diff nos indicadores**

Run:

```powershell
git diff origin/main -- strategies/donchian_zigzag.py utils/indicators.py
```

Expected: nenhuma saída.

**Step 2: Rodar validação completa**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/js/chart.test.mjs tests/js/market_context.test.mjs
.\.venv\Scripts\python.exe -m compileall -q api config core data database risk strategies trading utils main.py
```

Expected: tudo passa.

**Step 3: Registrar evidências do smoke demo**

Anotar horário, bot ID, contract ID demo, estados observados e resultado da parada, sem registrar PAT, OTP ou chave do dashboard.


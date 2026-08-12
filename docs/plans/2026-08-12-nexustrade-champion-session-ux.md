# NexusTrade Champion Session UX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expor no snapshot e no frontend a diferenca entre baseline OFF e sessao ON do Champion, incluindo a assertividade informativa da ultima hora.

**Architecture:** O backend continua usando `nexus_trade/repository.py` como fonte do snapshot duravel e adiciona blocos derivados para sessao do Champion e metricas da ultima hora. O frontend consome esses blocos via store, atualiza o card do Champion e preserva o modal existente como configuracao da proxima sessao.

**Tech Stack:** Python, SQLite, FastAPI, JavaScript modular, Node test runner, unittest.

---

### Task 1: Registrar o contrato aprovado

**Files:**
- Create: `docs/plans/2026-08-12-nexustrade-champion-session-ux-design.md`
- Create: `docs/NexusTrade-Learning/CHAMPION-SESSION-UX.md`
- Modify: `docs/NexusTrade-Learning/FRONTEND.md`

**Step 1: Salvar o design aprovado**

Descrever separacao entre:
- baseline OFF em DEMO `0.35`;
- sugestao persistida para o proximo ON;
- sessao ativa somente quando o Champion estiver ON;
- assertividade da ultima hora somente para `champion_baseline`.

**Step 2: Atualizar o contrato funcional**

Adicionar ao documento de frontend o resumo da nova UX operacional do Champion.

### Task 2: TDD do snapshot duravel

**Files:**
- Modify: `tests/test_nexus_trade_repository.py`
- Modify: `nexus_trade/repository.py`

**Step 1: Write the failing test**

Criar testes para garantir que `get_control_snapshot()` exponha:
- `champion_session` com `management_active=False` e `suggestion` quando OFF;
- `champion_session` com `management_active=True` e `active_management` quando ON;
- `champion_last_hour` com contagens e `accuracy` calculados somente a partir de trades
  fechados da lane `champion_baseline` dentro da ultima hora.

**Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_control_snapshot_exposes_champion_session_semantics tests.test_nexus_trade_repository.NexusTradeRepositoryTests.test_control_snapshot_reports_last_hour_accuracy_for_champion_only -v`

Expected: FAIL por ausencia dos novos campos.

**Step 3: Write minimal implementation**

Adicionar helper derivado no repository para calcular:
- sessao do Champion;
- resumo da ultima hora.

**Step 4: Run test to verify it passes**

Executar o mesmo comando e confirmar PASS.

### Task 3: TDD da API/store

**Files:**
- Modify: `tests/test_nexus_trade_api.py`
- Modify: `static/js/nexus_trade_store.js`
- Modify: `tests/js/nexus_trade_store.test.mjs`

**Step 1: Write the failing test**

Cobrir que:
- o endpoint `/api/v1/nexus-trade` entrega os novos campos;
- o store hidrata `championSession` e `championLastHour`.

**Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_api.NexusTradeApiTests.test_snapshot_exposes_champion_session_and_last_hour -v`

Run: `node --test tests/js/nexus_trade_store.test.mjs`

Expected: FAIL nos asserts dos novos campos.

**Step 3: Write minimal implementation**

Propagar os novos campos sem alterar o contrato antigo.

**Step 4: Run test to verify it passes**

Reexecutar os mesmos comandos.

### Task 4: TDD da view do Champion

**Files:**
- Modify: `tests/js/nexus_trade_view.test.mjs`
- Modify: `static/js/nexus_trade_view.js`
- Modify: `static/index.html`
- Modify: `static/styles.css`

**Step 1: Write the failing test**

Cobrir:
- card OFF exibindo baseline `US$ 0,35`, sugestao da proxima sessao e mensagem sem
  gerenciamento ativo;
- card ON exibindo gerenciamento da sessao;
- assertividade da ultima hora renderizada como informativa e exclusiva do Champion.

**Step 2: Run test to verify it fails**

Run: `node --test tests/js/nexus_trade_view.test.mjs`

Expected: FAIL nos textos e no novo modelo.

**Step 3: Write minimal implementation**

Adicionar o menor diff possivel para o card do Champion e reutilizar o modal atual.

**Step 4: Run test to verify it passes**

Run: `node --test tests/js/nexus_trade_view.test.mjs`

Expected: PASS.

### Task 5: Validacao final

**Files:**
- Modify: `docs/NexusTrade-Learning/FRONTEND.md`
- Modify: `docs/NexusTrade-Learning/CHAMPION-SESSION-UX.md`

**Step 1: Rodar a bateria relevante**

Run:
- `.\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_repository tests.test_nexus_trade_api -v`
- `node --test tests/js/nexus_trade_store.test.mjs tests/js/nexus_trade_view.test.mjs`
- `git diff --check`

**Step 2: Validar escopo protegido**

Confirmar que `strategies/donchian_zigzag.py` e `utils/indicators.py` nao mudaram.

**Step 3: Atualizar a documentacao de entrega**

Registrar implementacao, testes executados e impacto operacional em
`docs/NexusTrade-Learning/CHAMPION-SESSION-UX.md`.

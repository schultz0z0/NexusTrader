# Task 2 — regressão de fixtures após endurecimento de FKs

Data: 2026-08-10

## Causa

O endurecimento correto das conexões SQLite passou a executar com
`PRAGMA foreign_keys=ON`. Duas fixtures antigas dependiam implicitamente da
ausência dessa validação:

- `tests/test_order_ownership.py` criava `order_intents` para `bot-a` e
  `bot-b` sem linhas pai em `bot_instances`. A FK que falhava era
  `order_intents.bot_id -> bot_instances.id`.
- A regressão completa revelou a mesma classe de problema em
  `tests/test_risk_persistence.py`: os trades usavam `session-a` sem linha pai
  em `sessions`. A FK que falhava era `trades.session_id -> sessions.id`.

Não havia defeito em `DatabaseRepository.create_order_intent()` nem nos
guards Nexus. A origem era o setup dos testes, que construía grafos de dados
relacionalmente inválidos.

## RED observado

Com credenciais locais dummy, o teste solicitado reproduziu de forma estável:

```text
python -m unittest tests.test_order_ownership -v
Ran 9 tests
FAILED (errors=7)
sqlite3.IntegrityError: FOREIGN KEY constraint failed
```

Todos os sete erros ocorreram no primeiro `INSERT INTO order_intents`, antes
que o índice parcial de exclusão por `account_id` pudesse ser exercitado.

Após a primeira correção, a regressão completa expôs os quatro casos restantes:

```text
python -m unittest discover -s tests -v
Ran 276 tests
FAILED (failures=3, errors=4)
```

Os quatro errors eram a FK de `session-a` em `test_risk_persistence`. Os três
failures de `test_settings` foram causados apenas pelo `DEV_MODE=true` usado
naquela invocação; a execução final usou credenciais dummy completas sem
alterar o modo de runtime.

## Correção

- Adicionado helper local que provisiona explicitamente `bot-a` e `bot-b`
  por meio do `DatabaseRepository.create_bot()` real antes de criar intents.
- Adicionado provisionamento explícito e determinístico de `session-a` no
  setup da persistência de risco.
- A busca por `create_order_intent`, `upsert_trade` e
  `settle_trade_and_risk` confirmou que somente esses testes de persistência
  faziam as suposições inválidas afetadas.

Nenhuma FK, trigger ou validação foi removida ou relaxada; não houve uso de
`PRAGMA foreign_keys=OFF` nem criação silenciosa de pais em código de produção.
Donchian e Nexus Speed não foram alterados.

## GREEN e verificações

Executado com `.venv\Scripts\python.exe` e valores dummy para
`DERIV_APP_ID`, `DERIV_API_TOKEN`, `INTERNAL_API_TOKEN` e
`DASHBOARD_API_KEY`:

```text
python -m unittest tests.test_order_ownership -v
PASS — 9 testes

python -m unittest tests.test_order_ownership tests.test_trade_lifecycle tests.test_repository tests.test_nexus_trade_repository -v
PASS — 49 testes

python -m unittest tests.test_risk_persistence -v
PASS — 4 testes

python -m unittest discover -s tests -v
PASS — 276 testes

python -m compileall -q database nexus_trade tests/test_order_ownership.py tests/test_risk_persistence.py
PASS

git diff --check
PASS
```

A suíte completa mantém um `StarletteDeprecationWarning` preexistente sobre
`fastapi.testclient`; não houve failures nem errors.

## Arquivos e commit

- `tests/test_order_ownership.py`
- `tests/test_risk_persistence.py`
- `.superpowers/sdd/PHASE-1-BACKEND-DATA-PLAN/task-2-regression-report.md`

Commit local: `test: provision valid parents for FK fixtures`. Este relatório
faz parte do mesmo commit; o hash final é informado no handoff.

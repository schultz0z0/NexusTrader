# NexusTrade Recovery and Automatic Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execution is inline, in this session, without subagents, as explicitly requested by the user.

**Goal:** Recuperar contratos duráveis, contabilizar a campanha Trial ao vivo, automatizar o laboratório governado e exibir Bollinger no gráfico operacional.

**Architecture:** O runtime recupera o monitor pelo owner durável antes de retomar contratos. O repository materializa progresso a partir dos settlements. Um `LearningLabService` usa jobs persistidos para relatórios, dataset, treino, candidatos e rotação semanal fora do caminho M1. O frontend consome snapshot/eventos idempotentes e o gráfico reutiliza o feed R_100/M1.

**Tech Stack:** Python 3.11, asyncio, FastAPI, SQLite WAL/aiosqlite, scikit-learn, JavaScript ES modules, Lightweight Charts, Docker Compose, unittest e Node test runner.

## Global Constraints

- R_100, M1, duração 58 segundos, Bollinger obrigatória e ADX `<=22` são invariantes.
- Champion só evolui por aprovação humana; Trial pode rotacionar apenas na fronteira semanal.
- OFF opera Champion/Trial DEMO a USD 0,35; gerenciamento pertence somente ao Champion ON.
- No máximo um contrato ativo por lane.
- REAL permanece bloqueado durante QA.
- Nenhum push; Donchian e Nexus Speed permanecem protegidos.

---

### Task 1: Recuperação do monitor e liquidação após restart

**Files:**
- Modify: `nexus_trade/runtime.py`
- Modify: `tests/test_nexus_trade_runtime.py`

**Interfaces:**
- Consumes: owner durável, `_shared_demo_monitor`, lane ACTIVE.
- Produces: monitor roteado antes de `_resume_active_monitors()`.

- [x] Escrever teste que restaura Champion OFF ACTIVE no dispatcher DEMO compartilhado e exige chamada `monitor_contract`.
- [x] Executar o teste e observar ausência do monitor.
- [x] Implementar fallback explícito para `_shared_demo_monitor` somente quando a identidade coincide.
- [x] Provar settlement, IDLE, idempotência e ausência de segunda compra.
- [x] Executar regressão runtime/repository e commit local.

### Task 2: Progresso durável e vivo da campanha Trial

**Files:**
- Modify: `nexus_trade/repository.py`
- Modify: `static/js/nexus_trade_store.js`
- Modify: `tests/test_nexus_trade_repository.py`
- Modify: `tests/js/nexus_trade_store.test.mjs`

**Interfaces:**
- Produces: `active_campaigns[].progress={completed,target}` para a campanha Trial.

- [x] Escrever RED com trades Champion e Trial de campanhas distintas e conferir contagem exata.
- [x] Escrever RED JS que seleciona apenas `challenger_trial` e incrementa por trade settled idempotente.
- [x] Implementar query transacional e atualização do store.
- [x] Rodar regressão API/store e commit local.

### Task 3: Orquestrador automático do laboratório

**Files:**
- Create: `nexus_trade/learning_lab.py`
- Modify: `nexus_trade/runtime.py`
- Modify: `database/repository.py`
- Modify: `api/live_store.py`
- Modify: `tests/test_nexus_trade_learning_lab.py`
- Modify: `tests/test_nexus_trade_runtime.py`

**Interfaces:**
- Produces: `LearningLabService.run_due(now)`, jobs idempotentes, tentativas e candidatos SHADOW.
- Consumes: `DatasetBuilder`, `Trainer`, `SQLiteTrialLedger`, `CandidateRegistry`, `DurableReportScheduler`, `PromotionService.replace_trial`.

- [x] Escrever RED para nenhuma amostra, dataset causal válido, treino repetido/idempotente, restart e falha fail-closed.
- [x] Persistir feature snapshot correlacionado à decisão e settlement antes de montar rows.
- [x] Construir dataset apenas de rows completas/settled da campanha, com proveniência exata.
- [x] Treinar candidatos determinísticos e registrar todas as tentativas.
- [x] Na fronteira semanal, avaliar/rotacionar somente SHADOW executável elegível; nunca Champion.
- [x] Publicar estado do laboratório e integrar lifecycle start/stop sem bloquear M1.
- [x] Rodar regressão learning/reports/promotion/runtime e commit local.

### Task 4: Bollinger e observabilidade visual

**Files:**
- Modify: `static/js/nexus_trade_operations.js`
- Modify: `static/js/chart.js`
- Modify: `static/js/nexus_trade_store.js`
- Modify: `static/js/nexus_trade_view.js`
- Modify: `static/styles.css`
- Modify: `tests/js/nexus_trade_operations.test.mjs`
- Modify: `tests/js/chart.test.mjs`
- Modify: `tests/js/nexus_trade_view.test.mjs`

**Interfaces:**
- Produces: linhas superior/SMA/inferior 20/2/SMA e card de atividade do laboratório.

- [x] Escrever RED para valores Bollinger determinísticos e séries visíveis.
- [x] Garantir contraste, legenda textual e persistência após reconnect.
- [x] Mostrar tentativa/candidato/ablação/rotação sem controles no Trial.
- [x] Rodar suíte JS e contrato frontend; commit local.

### Task 5: Gate integral de documentação, Docker e navegador

**Files:**
- Modify: `docs/NexusTrade-Learning/PHASE-2-VALIDATION.md`
- Modify: `docs/NexusTrade-Learning/PHASE-2-IMPLEMENTATION-RUNBOOK.md`
- Modify: `docs/NexusTrade-Learning/RECOVERY-LAB-AUTOMATION-PLAN.md`

**Interfaces:**
- Produces: decisão GO/NO-GO, instruções localhost/VPS e teste manual.

- [x] Rodar full Python, full JS, compileall, pip check, compose config e diffs protegidos.
- [ ] Rebuild DEMO-only da revisão final na porta 8993 preservando o volume real de validação.
- [x] Provar no navegador liquidação, gerenciamento, progresso, Bollinger e laboratório sem F5 na imagem operacional anterior.
- [x] Reiniciar API/bot e repetir snapshot/contratos/campanha/jobs na imagem operacional anterior.
- [ ] Repetir o scan de banco, eventos e logs na imagem da revisão final; o daemon Docker está bloqueado nesta sessão gerenciada.
- [x] Atualizar instruções VPS/manual e registrar evidências sanitizadas.

As duas caixas restantes são um gate de implantação da revisão final, não uma lacuna de
código. Os testes completos, a API isolada e o navegador da imagem anterior estão
verdes; o rebuild deve ser executado por `scripts/rebuild_local_docker.ps1` em um shell
com acesso ao pipe do Docker antes do deploy.

## Completion Gate

Nenhuma task é concluída apenas por teste unitário. Cada alteração precisa de regressão proporcional; o gate final exige Docker e navegador reais, logs e banco coerentes, documentação conferida e worktree limpo, sem push.

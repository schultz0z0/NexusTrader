# NexusTrade Learning Regression Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Corrigir as regressões atuais de `learning_lab` e `tick_archive`, validar o NexusTrade localmente e atualizar a documentação operacional conforme o estado real do código.

**Architecture:** O ciclo mantém o menor diff possível e ataca primeiro a linha temporal do laboratório, depois a recuperação causal do arquivo de ticks. Cada correção é feita por TDD, seguida de regressão proporcional, documentação incremental e validação final em navegador.

**Tech Stack:** Python 3.11, unittest, SQLite, asyncio, FastAPI, JavaScript ES modules, PowerShell, Docker Compose.

---

### Task 1: Isolar e corrigir regressões do learning lab

**Files:**
- Modify: `nexus_trade/learning_lab.py`
- Modify: `nexus_trade/scheduler.py` (somente se a causa raiz exigir)
- Modify: `tests/test_nexus_trade_learning_lab.py`
- Modify: `docs/NexusTrade-Learning/PHASE-2-VALIDATION.md`

**Step 1: Rodar apenas os testes falhando do learning lab**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_learning_lab.LearningEvidenceTests.test_daily_job_trains_content_addressed_shadow_from_complete_rows tests.test_nexus_trade_learning_lab.LearningEvidenceTests.test_due_daily_job_closes_one_real_report_and_exposes_its_identity tests.test_nexus_trade_learning_lab.LearningEvidenceTests.test_due_daily_job_records_insufficient_data_once_across_restart tests.test_nexus_trade_learning_lab.LearningEvidenceTests.test_failed_daily_job_is_retried_after_restart_instead_of_stalling_forever -v`

Expected: FAIL com `ValueError: now cannot precede the durable scheduler cursor`.

**Step 2: Investigar a causa raiz**

Ler o cursor persistido, `campaign["started_at"]`, `_last_completed_boundary(...)` e o contrato de `due_windows(...)`. Confirmar qual valor está ficando à frente de `now`.

**Step 3: Escrever ou ajustar o teste mínimo necessário**

Garantir um caso explícito para cursor durável no limite, restart e reexecução idempotente sem erro temporal indevido.

**Step 4: Implementar a correção mínima**

Corrigir a origem do cursor inconsistente sem enfraquecer o fail-closed para casos realmente inválidos.

**Step 5: Rodar a suíte focada do learning lab**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_learning_lab -v`

Expected: PASS.

**Step 6: Atualizar a validação incremental**

Registrar no documento de validação o que foi reproduzido, a causa raiz confirmada e a evidência local renovada.

### Task 2: Isolar e corrigir regressões do tick archive

**Files:**
- Modify: `nexus_trade/tick_archive.py`
- Modify: `tests/test_nexus_trade_tick_archive.py`
- Modify: `docs/NexusTrade-Learning/PHASE-1-VALIDATION.md`

**Step 1: Rodar apenas os testes falhando do tick archive**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_tick_archive.TickArchiveTests.test_legacy_uuid_orphan_after_manifest_tail_gets_the_next_causal_sequence tests.test_nexus_trade_tick_archive.TickArchiveTests.test_replay_uses_persisted_sequence_when_same_day_names_sort_in_reverse_order tests.test_nexus_trade_tick_archive.TickArchiveTests.test_restart_rebuilds_global_tail_for_retry_deduplication_and_backdating_rejection -v`

Expected: FAIL com `ValueError: manifest diverges from published segment`.

**Step 2: Investigar a causa raiz**

Comparar o manifesto persistido com os dados reconstruídos na recuperação, incluindo `id`, `path`, `sha256`, `byte_count` e `segment_sequence`.

**Step 3: Escrever ou ajustar o teste mínimo necessário**

Cobrir o caso exato em que o segmento publicado é legítimo, mas a revalidação atual o rejeita na recuperação.

**Step 4: Implementar a correção mínima**

Corrigir a equivalência entre manifesto e segmento físico sem abrir brecha para manifesto realmente divergente.

**Step 5: Rodar a suíte focada do tick archive**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_nexus_trade_tick_archive -v`

Expected: PASS.

**Step 6: Atualizar a validação incremental**

Registrar a correção do restart causal e da recuperação do manifesto nos documentos de validação.

### Task 3: Fechar regressão focal do NexusTrade

**Files:**
- Modify: `docs/NexusTrade-Learning/PHASE-2-VALIDATION.md`
- Modify: `docs/NexusTrade-Learning/PHASE-2-IMPLEMENTATION-RUNBOOK.md`

**Step 1: Rodar a suíte focal do NexusTrade**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_nexus_trade*.py" -v`

Expected: PASS.

**Step 2: Rodar verificações complementares**

Run: `.\.venv\Scripts\python.exe -m compileall -q api core data database nexus_trade strategies trading tests`

Run: `.\.venv\Scripts\python.exe -m pip check`

Run: `docker compose config --quiet`

Expected: exit code zero em todos.

**Step 3: Atualizar documentação**

Registrar os comandos, os resultados e o status real do código após as correções.

### Task 4: Validar localmente em navegador

**Files:**
- Modify: `docs/NexusTrade-Learning/PHASE-2-VALIDATION.md`
- Modify: `docs/NexusTrade-Learning/FRONTEND.md` (somente se a evidência exigir ajuste factual)

**Step 1: Subir o ambiente local seguro**

Run: `powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1`

Expected: API em loopback, DEMO forçado, REAL bloqueado.

**Step 2: Validar o NexusTrade no navegador**

Abrir `http://127.0.0.1:8990`, conferir snapshot, estados principais, console limpo, reconnect e ausência de erro visível.

**Step 3: Verificar logs**

Inspecionar logs da API/bot e confirmar ausência de traceback, tentativa REAL e erro novo associado às correções.

**Step 4: Atualizar documentação**

Registrar a evidência manual e qualquer limite ainda restante.

### Task 5: Fechar checklist de VPS

**Files:**
- Modify: `docs/NexusTrade-Learning/PHASE-2-IMPLEMENTATION-RUNBOOK.md`
- Modify: `docs/NexusTrade-Learning/PHASE-2-VALIDATION.md`

**Step 1: Comparar o estado final do código com o runbook**

Garantir que os comandos e pré-condições reflitam o estado efetivamente validado localmente.

**Step 2: Escrever checklist operacional enxuto**

Incluir sequência de verificação pré-build, build, healthcheck, logs, reconnect e rollback para a VPS.

**Step 3: Fazer a checagem final**

Run: `git diff --check`

Expected: PASS.

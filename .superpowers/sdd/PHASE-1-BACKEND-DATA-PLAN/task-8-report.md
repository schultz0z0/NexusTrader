# Task 8 — relatórios, gates, campanha e exports

Data: 2026-08-10
Branch: `feature/nexustrade-learning`
Escopo: métricas governadas, calendário persistente, gates conservadores, snapshots imutáveis, histórico e exports. Nenhuma promoção foi implementada.

## RED

- Métricas: `tests.test_nexus_trade_reports` falhou com `ModuleNotFoundError: nexus_trade.metrics` antes da implementação.
- Gates: `tests.test_nexus_trade_gates` falhou com `ModuleNotFoundError: nexus_trade.gates`.
- Scheduler: módulo ausente no primeiro RED; o ciclo seguinte expôs um handle SQLite não fechado no Windows, corrigido antes do GREEN.
- Relatórios: `ImmutableReportTests` falhou inicialmente por módulo ausente e depois provou conflito de conteúdo no mesmo slot alinhado.
- Exports: a primeira execução detectou `openpyxl` ausente na `.venv`; a dependência já estava exatamente travada em `requirements.txt`. Foi instalada somente com `.\.venv\Scripts\python.exe -m pip install openpyxl==3.1.5`. Após isso, o RED esperado foi `ModuleNotFoundError: nexus_trade.exports`.
- API: o novo teste de histórico/download retornou `404` antes das rotas finas serem adicionadas.

## GREEN e decisões

- `calculate_lane_metrics` agrega contagens e dinheiro bruto antes das taxas; expõe N total/decisivo, wins/losses/ties, accuracy descritiva, stake, expectancy normalizada, payout, PF, drawdown, recovery, rolling 50 e loss streak. Dados não finitos, stake zero, outcomes inválidos e settlement não finalizado falham fechados.
- `BrasiliaSchedule` usa `zoneinfo`/`America/Sao_Paulo`, persiste instantes UTC e offsets locais; `DurableReportScheduler` usa claim SQLite transacional, lease recuperável e jobs idempotentes para catch-up/restart/concorrência, com relógio injetável.
- `PromotionGateEvaluator` ordena amostra/integridade/comparabilidade antes dos gates econômicos; aplica +2 p.p., PF 1,10, bootstrap pareado em blocos temporais, risco, estabilidade, DSR/PBO e orçamento de uma família. Ausência/NaN/Inf resulta em `INCONCLUSIVE`; accuracy não é gate.
- `ReportService` gera snapshots congelados e content-addressed, faz compare-and-insert por slot alinhado e instala guards SQLite contra update/delete. A coleta persistida usa `nexus_decisions.signal_epoch` com fim exclusivo, portanto settlement tardio permanece no dia da decisão. O contador acumulado filtra a campanha Trial e não zera na virada visual semanal.
- `ReportExporter` gera exatamente sete CSVs e sete abas XLSX do mesmo snapshot. Fórmulas são neutralizadas, nomes internos são fixos, conteúdo é determinístico, não há VBA/macros, e secrets/caminhos/non-finite são rejeitados.
- A API expõe lista/detalhe, lookup semanal alinhado e downloads CSV ZIP/XLSX por serviços; nenhuma rota de promoção foi adicionada.

## Evidência final

- Focados: `python -m unittest tests.test_nexus_trade_reports tests.test_nexus_trade_gates tests.test_nexus_trade_exports -v` — **18/18**.
- Regressões learning/runtime/API/repository/dispatcher/strategy/Nexus Speed/Donchian — **198/198**.
- Full Python: `python -m unittest discover -s tests -v` — **393/393**.
- JavaScript: `node --test tests/js/*.test.mjs` — **17/17**.
- `python -m compileall -q api backtest core data database risk strategies trading nexus_trade` — exit 0.
- `python -m pip check` — `No broken requirements found.`
- `docker compose config --quiet` — exit 0 usando `.env` temporário com valores dummy, removido imediatamente após o check.
- `git diff --check` — exit 0.
- `git diff -- strategies/donchian_zigzag.py utils/indicators.py` — vazio; robôs protegidos permanecem inalterados.
- Nenhum acesso Deriv, ordem REAL, push ou deploy foi realizado.

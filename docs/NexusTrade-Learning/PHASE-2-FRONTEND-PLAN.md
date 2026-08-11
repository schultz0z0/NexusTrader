# NexusTrade Phase 2 — Frontend Implementation Plan

> **Execution mode approved by the user:** inline execution in one Codex session, without subagents. Use `superpowers:executing-plans`, TDD, the checklist below and local commits after each task. Never push.

**Goal:** Integrar o NexusTrade ao painel atual com operação do Champion, relatórios, evolução, aprovação e histórico completos, responsivos e atualizados sem F5.

**Architecture:** O frontend permanece HTML/CSS/JavaScript modular sem framework. Novos módulos `nexus_trade_api.js`, `nexus_trade_store.js` e `nexus_trade_view.js` consomem exclusivamente os contratos congelados da Fase 1; `app.js` apenas roteia seleção/navegação. O shell, rail, tokens, tipografia e componentes visuais existentes são preservados.

**Tech Stack:** HTML5, CSS, JavaScript ES modules, WebSocket existente, Node test runner, Python unittest para contrato HTML e API FastAPI em localhost.

## Global Constraints

- NexusTrade é singleton fixo e não aparece em criação, duplicação ou seletor de estratégia.
- Donchian e Nexus Speed mantêm fluxo e aparência atuais sem regressão.
- O usuário controla somente Champion; Trial não possui botão de start/stop/configuração.
- `R_100`, M1 e 58 segundos aparecem como valores fixos, nunca como selects editáveis.
- Todo estado relevante do backend aparece sem F5 e permanece no histórico.
- Todos os relatórios mostram N, wins, losses, empates e assertividade com denominador.
- Ações de promoção/rollback obedecem às travas retornadas pelo backend; frontend nunca simula autorização.
- Segredos, tokens Deriv e OTP não entram no DOM, WebSocket, export ou log do navegador.
- A credencial humana de governança nunca é persistida. Ela existe apenas no campo `password` durante a confirmação, segue no header `X-Nexus-Human-Key` e é apagada do DOM e da memória no `finally` da requisição.
- Estados compartilháveis usam apenas IDs públicos no URL: aba, `week_start`, `report_id`, `campaign_id` e `proposal_id`. Credenciais e payloads brutos nunca entram no URL.
- Visualizações usam SVG/DOM leve e tabelas equivalentes; nenhum valor essencial depende de hover, animação ou cor isolada.
- Cada task termina com testes focados e regressão proporcional. Tasks com UI são abertas na stack Docker local e inspecionadas em navegador real; o fechamento repete a matriz integral.

---

### Task 0: Congelar contratos e runbook de execução

**Files:**
- Create: `docs/NexusTrade-Learning/PHASE-2-IMPLEMENTATION-RUNBOOK.md`
- Modify: `docs/NexusTrade-Learning/PHASE-2-FRONTEND-PLAN.md`
- Modify: `docs/NexusTrade-Learning/FRONTEND.md`

**Interfaces:**
- Consumes: rotas FastAPI e os oito eventos `nexus.*` da Fase 1.
- Produces: contrato vinculante de API, segurança, deploy e QA manual da Fase 2.

- [x] **Step 1: Verificar as rotas reais**

Snapshot/modo: `GET /api/v1/nexus-trade`, `POST /mode`, `POST /real-confirmation`
e `POST /emergency-stop`. Catálogos: `/versions`, `/campaigns`, `/reports`,
`/reports/weekly/{aligned_week}`, `/reports/{id}`, `/proposals` e `/exports`.
Ações: `POST /proposals/{id}/approve`, `POST /proposals/{id}/reanalyze` e
`POST /rollback`.

- [x] **Step 2: Verificar os eventos reais**

`nexus.runtime`, `nexus.decision`, `nexus.trade`, `nexus.campaign`, `nexus.report`,
`nexus.trial_changed`, `nexus.proposal` e `nexus.version_changed`, todos com
`event_id`, `schema_version=1`, `snapshot_version>=1`, `bot_id=nexus-trade` e
`payload` sanitizado.

- [x] **Step 3: Congelar recomendações e relatórios**

Os estados reais são `EVOLVE`, `REANALYZE` e `INCONCLUSIVE`. Relatórios usam `days`,
`full_totals`, `accumulated_progress`, `diffs`, `gates`, `recommendation`,
`recommendation_reasons`, `audit` e `disclosure`.

- [x] **Step 4: Registrar deploy e testes manuais**

O runbook define localhost, VPS, backup, build, health, restart, rollback, logs,
breakpoints, teclado, WebSocket, relatórios, exports e governança sem operação REAL.

- [ ] **Step 5: Commit**

```bash
git add docs/NexusTrade-Learning
git commit -m "docs: freeze NexusTrade frontend execution contracts"
```

### Task 1: Contrato do cliente, store e roteamento da view

**Files:**
- Create: `static/js/nexus_trade_api.js`
- Create: `static/js/nexus_trade_store.js`
- Create: `static/js/nexus_trade_view.js`
- Modify: `static/js/app.js`
- Create: `tests/js/nexus_trade_store.test.mjs`
- Create: `tests/js/nexus_trade_api.test.mjs`

**Interfaces:**
- Consumes: endpoints e eventos `nexus.*` congelados na Fase 1.
- Produces: `nexusTradeApi`, `createNexusTradeStore`, `mountNexusTradeView`.

- [ ] **Step 1: Escreva testes de normalização e idempotência**

```javascript
const store = createNexusTradeStore();
store.apply({ type: "nexus.trial_changed", event_id: "e1", payload });
store.apply({ type: "nexus.trial_changed", event_id: "e1", payload });
assert.equal(store.get().auditEvents.length, 1);
assert.equal(store.get().campaign.progress.completed, 0);
```

- [ ] **Step 2: Teste cliente e erros estruturados**

Garanta API key apenas em header, downloads binários, 409 de trava operacional e ausência
de credencial em URL.

- [ ] **Step 3: Rode e confirme falha**

Run: `node --test tests/js/nexus_trade_store.test.mjs tests/js/nexus_trade_api.test.mjs`

- [ ] **Step 4: Implemente módulos**

```javascript
export function createNexusTradeStore(initial = {}) {
  let state = structuredClone(initial);
  const seen = new Set();
  return { get: () => state, hydrate, apply, subscribe };
}
```

`app.js` seleciona a view pelo ID fixo `nexus-trade`, sem inserir `nexus_trade` no fluxo
de formulários de estratégias.

- [ ] **Step 5: Rode testes JS existentes e novos**

Run: `node --test tests/js/*.test.mjs`

- [ ] **Step 6: Commit**

```bash
git add static/js tests/js
git commit -m "feat: add NexusTrade frontend data layer"
```

### Task 2: Singleton no rail e painel operacional do Champion

**Files:**
- Modify: `static/index.html`
- Modify: `static/styles.css`
- Modify: `static/js/nexus_trade_view.js`
- Modify: `tests/test_frontend_contract.py`
- Create: `tests/js/nexus_trade_view.test.mjs`

**Interfaces:**
- Produces: rail fixo, cabeçalho da versão, status das lanes e controles Champion.

- [ ] **Step 1: Escreva contrato HTML**

```python
self.assertIn('id="nexus-trade-view"', html)
self.assertIn('data-nexus-action="champion-toggle"', html)
self.assertIn('data-nexus-fixed-symbol="R_100"', html)
self.assertNotIn('value="nexus_trade"', strategy_select_html)
```

- [ ] **Step 2: Escreva testes de renderização de estados**

Cubra `OFF — aprendendo em DEMO`, `ON — DEMO`, `ON — REAL`, `aguardando liquidação`,
`promoção pendente`, `parada total`, Trial ativo oculto de controles e Champion V1/Vn.

- [ ] **Step 3: Rode e confirme falha**

Run: `python -m unittest tests.test_frontend_contract -v`

Run: `node --test tests/js/nexus_trade_view.test.mjs`

- [ ] **Step 4: Implemente markup e estilo usando o KV existente**

Reutilize `.shell`, `.command-bar`, `.bot-rail`, `.workspace`, `.panel`, `.panel-head`,
`.eyebrow`, `.status-chip` e `.primary-button`. Novas classes começam com `.nexus-` e
usam os tokens de cor/tipografia já existentes.

- [ ] **Step 5: Implemente controles reais**

Champion toggle chama `/mode`; OFF mostra DEMO USD 0,35; ON usa conta/gerenciamento
selecionados. Trial mostra somente status, versão, campanha e progresso, sem ação.

- [ ] **Step 6: Rode regressão frontend**

Run: `python -m unittest tests.test_frontend_contract -v`

Run: `node --test tests/js/*.test.mjs`

- [ ] **Step 7: Commit**

```bash
git add static tests
git commit -m "feat: render protected NexusTrade champion controls"
```

### Task 3: Relatórios diários, semanais e evolução acumulada

**Files:**
- Modify: `static/index.html`
- Modify: `static/styles.css`
- Modify: `static/js/nexus_trade_view.js`
- Create: `static/js/nexus_trade_metrics.js`
- Create: `tests/js/nexus_trade_metrics.test.mjs`

**Interfaces:**
- Produces: abas `Relatórios` e `Evolução`, tabelas Champion × Trial e calendário semanal.

- [ ] **Step 1: Teste formatação sem recalcular fonte de verdade**

```javascript
assert.equal(formatAccuracy({ wins: 174, losses: 126 }), "174/300 (58,00%)");
assert.equal(formatMoney(1.05), "US$ 1,05");
```

O cliente exibe agregados do snapshot; não tira média de percentuais diários.

- [ ] **Step 2: Teste calendário por semana**

Seleção aceita apenas `week_start` de segunda-feira 10:00 Brasília e reabre snapshots
imutáveis. Navegação anterior/próxima mantém URL/deep-link.

- [ ] **Step 3: Rode e confirme falha**

Run: `node --test tests/js/nexus_trade_metrics.test.mjs tests/js/nexus_trade_view.test.mjs`

- [ ] **Step 4: Implemente os dois níveis**

Relatórios exibem cada dia e `SEMANA COMPLETA`. Evolução exibe campanha acumulada,
semanas incluídas, `N_total/300`, tempo, versões e estado da recomendação.

- [ ] **Step 5: Renderize todas as métricas obrigatórias**

Inclua N total/decisivo, wins/losses/empates, assertividade, breakeven, payout, capital
arriscado, P&L, ROI, expectancy, PF, drawdown, recovery, pior bloco, loss streak,
estabilidade, DSR/PBO e gates.

- [ ] **Step 6: Rode testes**

Run: `node --test tests/js/*.test.mjs`

- [ ] **Step 7: Commit**

```bash
git add static tests/js
git commit -m "feat: show NexusTrade reports and evolution metrics"
```

### Task 4: Diff explicável, aprovação, reanálise e rollback

**Files:**
- Modify: `static/js/nexus_trade_view.js`
- Modify: `static/styles.css`
- Create: `static/js/nexus_trade_diff.js`
- Create: `tests/js/nexus_trade_actions.test.mjs`

**Interfaces:**
- Produces: diff before/after, gates e ações governadas por revisão.

- [ ] **Step 1: Teste diff completo**

```javascript
const rows = buildDiff(champion, trial);
assert.deepEqual(rows.find(r => r.path === "bollinger.period"), {
  path: "bollinger.period", before: 20, after: 18, change: "modified"
});
```

Cubra indicador adicionado/removido, configs, features, regras, modelo e uma família de
mudança.

- [ ] **Step 2: Teste travas e confirmação reforçada**

`Aprovar` fica desabilitado com Champion ON, intent/contrato, revisão vencida ou gate
duro. Override de gate negociável exige segunda confirmação exibindo falhas.

- [ ] **Step 3: Rode e confirme falha**

Run: `node --test tests/js/nexus_trade_actions.test.mjs`

- [ ] **Step 4: Implemente ações com optimistic UI proibida**

A versão só muda após resposta/evento confirmado do backend. Em 409, recarregue snapshot
e mostre o motivo. Reanálise, rollback e aprovação exibem audit ID final.

- [ ] **Step 5: Implemente Trial A → B**

Mostre `SUPERSEDED`, motivo, hashes, diff, toast/badge e novo `0/300`; se mantido,
mostre explicitamente `Trial mantido` e a justificativa.

- [ ] **Step 6: Rode testes**

Run: `node --test tests/js/*.test.mjs`

- [ ] **Step 7: Commit**

```bash
git add static tests/js
git commit -m "feat: add governed NexusTrade evolution actions"
```

### Task 5: Histórico, exportação e tempo real sem F5

**Files:**
- Modify: `static/js/nexus_trade_api.js`
- Modify: `static/js/nexus_trade_store.js`
- Modify: `static/js/nexus_trade_view.js`
- Create: `tests/js/nexus_trade_realtime.test.mjs`

**Interfaces:**
- Produces: histórico semanal, download CSV/XLSX e reconciliação WebSocket.

- [ ] **Step 1: Teste eventos fora de ordem e reconnect**

Snapshot com revisão maior prevalece; eventos duplicados ou anteriores são ignorados;
reconnect busca snapshot antes de voltar a aceitar ações.

- [ ] **Step 2: Teste downloads**

Nome contém semana/campanha/hash; MIME e extensão são preservados; erro não gera arquivo
vazio.

- [ ] **Step 3: Rode e confirme falha**

Run: `node --test tests/js/nexus_trade_realtime.test.mjs`

- [ ] **Step 4: Implemente atualização transversal**

Toda alteração de lane, campanha, relatório, gate, proposta, versão, erro ou rollback
atualiza cabeçalho, tabelas, ações e histórico na mesma transação de store.

- [ ] **Step 5: Rode testes**

Run: `node --test tests/js/*.test.mjs`

- [ ] **Step 6: Commit**

```bash
git add static tests/js
git commit -m "feat: synchronize NexusTrade history and exports"
```

### Task 6: QA visual, acessibilidade, localhost e regressão integral

**Files:**
- Modify: `static/styles.css`
- Modify: `tests/test_frontend_contract.py`
- Create: `docs/NexusTrade-Learning/PHASE-2-VALIDATION.md`

**Interfaces:**
- Produces: evidência final de responsividade, acessibilidade, realtime e isolamento.

- [ ] **Step 1: Execute verificações automatizadas**

Run: `node --check static/js/app.js`

Run: `node --test tests/js/*.test.mjs`

Run: `python -m unittest discover -s tests -v`

Expected: PASS, inclusive Donchian/Nexus Speed.

- [ ] **Step 2: Suba stack local seguro**

Run: `powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1`

Abra `http://127.0.0.1:8990`; use somente conta DEMO e `ALLOW_REAL_TRADING=false`.

- [ ] **Step 3: Valide visualmente breakpoints e estados**

Verifique desktop largo, notebook, tablet e celular; carregando, vazio, acumulando,
proposta, Trial trocado, erro, reconnect, OFF/DEMO, ON/DEMO e representação ON/REAL sem
iniciar compra REAL.

- [ ] **Step 4: Valide acessibilidade**

Navegação completa por teclado, foco visível, `aria-live` para status/toasts, labels de
tabelas, contraste do KV e diálogo com foco preso/restaurado.

- [ ] **Step 5: Valide realtime e restart**

Mantenha a página aberta durante fechamento de relatório e restart dos containers;
confirme reconexão, snapshot coerente e atualização sem F5.

- [ ] **Step 6: Registre evidências e GO/NO-GO**

`PHASE-2-VALIDATION.md` registra comandos, resoluções, cenários, screenshots locais sem
segredos, defeitos encontrados/corrigidos e decisão final.

- [ ] **Step 7: Commit**

```bash
git add static tests docs/NexusTrade-Learning/PHASE-2-VALIDATION.md
git commit -m "test: validate complete NexusTrade frontend"
```

## Phase 2 completion gate

- Todos os estados e transições aparecem sem F5.
- Relatórios e evolução exibem métricas/diffs/gates completos e corretos.
- Aprovação, reanálise, Trial automático, rollback e exports funcionam por API real local.
- Interface é responsiva, acessível e coerente com o KV atual.
- Fluxos existentes de Donchian e Nexus Speed permanecem inalterados.
- Nenhuma compra REAL foi executada durante QA.

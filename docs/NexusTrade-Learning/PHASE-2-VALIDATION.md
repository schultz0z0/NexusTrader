# NexusTrade Phase 2 — Validation record

## Decision

**GO** em 2026-08-11 09:30 BRT para integrar a interface NexusTrade ao ambiente de
homologação. Nenhuma ordem REAL foi autorizada ou enviada durante a Fase 2.

Branch local: `feature/nexustrade-frontend`  
Base validada da Fase 1: `9b3232290c22a0d878e06ad6df3c8db465e57662`

## Automated evidence

| Gate | Result |
| --- | --- |
| Python full suite | 522/522 PASS em 83,581 s |
| JavaScript full suite | 50/50 PASS |
| `node --check static/js/app.js` | PASS |
| Python `compileall` | PASS |
| `pip check` | PASS, sem dependências quebradas |
| `docker compose config --quiet` | PASS |
| `git diff --check` | PASS |
| Donchian, indicators e Nexus Speed | zero diff desde a base da Fase 1 |

O conjunto cobre store idempotente, eventos fora de ordem, reconciliação REST após
reconnect, snapshots imutáveis, métricas, gates, diffs, ações humanas, CAS 409,
reanálise, promoção, rollback, exports e contratos HTML.

## Docker and browser evidence

A imagem local final foi executada como projeto isolado
`nexustrader-phase2-task2`, somente API, em `127.0.0.1:8992`. O container ficou
`healthy`, `restart_count=0` e REAL permaneceu bloqueado por
`ALLOW_REAL_TRADING=false` e `REAL_MAX_STAKE_USD=0`.

Matriz visual inspecionada no navegador real:

| Viewport | Page overflow | Touch targets | Result |
| --- | --- | --- | --- |
| 1440×900 | não | desktop 36/38 px | PASS |
| 1024×768 | não | desktop 36/38 px | PASS |
| 768×1024 | não | comandos e abas 44 px | PASS após correção |
| 390×844 | não | comandos, abas e exports 44 px | PASS |
| 360×800 | não | comandos e abas 44 px | PASS |

Estados comprovados na UI: singleton fixo, Champion OFF, duas lanes independentes,
R_100/M1/58s, relatório vazio e preenchido, sete dias mais total semanal, 20 métricas,
19 gates, evolução acumulada 327/300 e 8/7 dias, diff explicável, proposta pendente,
reanálise governada e histórico semanal.

Validações funcionais reais no banco sintético local:

- reanálise retornou `COMMITTED`, sem atualização otimista;
- credencial humana foi apagada do campo e não apareceu no DOM ou logs;
- CSV ZIP e XLSX foram baixados com os nomes imutáveis devolvidos pelo servidor;
- restart da API com a página aberta preservou semana, 20 métricas e ações após
  reconciliação, sem F5;
- evento `nexus.campaign` válido alterou o painel de `0/300` para `42/300` sem F5;
- console do navegador ficou sem erros;
- logs finais não continham traceback, ticket WebSocket, chaves ou credencial humana.

O único `ERROR` do container isolado foi o 401 esperado do token Deriv fictício usado
para impedir acesso a qualquer conta.

## Defects found and corrected during validation

1. Campo de confirmação reforçada ignorava `hidden` por precedência CSS.
2. Relatório oficial guarda `complete_days` em `accumulated_progress`; a promoção lia
   um campo superior inexistente.
3. Uvicorn podia registrar o ticket WebSocket no access log; o perfil usa log protocolar
   sem access log.
4. Cliente chamava export `zip`/`csv_zip`, mas a rota real exige `csv.zip`.
5. Arquivo HTTP vazio poderia chegar ao fluxo de download.
6. Ações podiam aparentar disponibilidade durante reconnect antes da hidratação REST.
7. Abas de tablet tinham 38 px em vez do alvo mínimo de 44 px.
8. Diálogo de governança não prendia foco nem fechava por Escape/restaurando o controle.

Todos ganharam reprodução automatizada RED e verificação GREEN.

## Manual localhost acceptance

1. Copie `.env.example` para uma configuração local fora do Git e defina credenciais
   distintas para dashboard, serviço interno e governança humana.
2. Comece obrigatoriamente com `DERIV_ACCOUNT_TYPE=demo`,
   `ALLOW_REAL_TRADING=false` e `REAL_MAX_STAKE_USD=0`.
3. Execute `powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1` e abra
   `http://127.0.0.1:8990`.
4. Autentique, selecione `NexusTrade` e confirme o contrato fixo R_100/M1/58s, OFF
   operando em DEMO a USD 0,35 e ausência de controles na lane Trial.
5. Teste ON/DEMO, parada segura, parada total, relatório diário/semanal, histórico por
   segunda-feira, CSV ZIP, XLSX, diff, gates e reanálise.
6. Reinicie API e bot separadamente mantendo a página aberta. Ações devem ficar
   indisponíveis enquanto o snapshot é reconciliado e voltar sem F5.
7. Repita a matriz de viewport e navegação somente por teclado. Tab deve permanecer no
   diálogo; Escape deve fechá-lo e devolver foco ao botão de origem.

## Optional controlled REAL acceptance

Este passo não faz parte do GO automatizado. Faça-o somente após DEMO passar, com o
Champion parado, nenhuma posição aberta e autorização consciente do responsável pela
conta.

1. Faça backup do volume e registre saldo, conta, stake, stop e horário.
2. Configure temporariamente `ALLOW_REAL_TRADING=true` e
   `REAL_MAX_STAKE_USD=0.35`; reinicie os serviços.
3. Selecione explicitamente a conta REAL, confira o badge vermelho, mantenha o Trial em
   DEMO e confirme a frase de segurança exibida pelo aplicativo.
4. Inicie somente o Champion e observe no máximo um contrato de USD 0,35. Confirme
   R_100, duração 58 s, uma posição por lane, correlação e liquidação.
5. Pare o Champion imediatamente após o cenário, volte para DEMO, restaure
   `ALLOW_REAL_TRADING=false` e `REAL_MAX_STAKE_USD=0`, reinicie e verifique o snapshot.
6. Se qualquer dado divergir, use parada total, não repita a ordem e preserve logs para
   diagnóstico.

## VPS acceptance and rollback

Use o procedimento versionado em `PHASE-2-IMPLEMENTATION-RUNBOOK.md`. Faça backup antes
do build, valide primeiro com REAL bloqueado, execute health/log/restart/downloads pelo
domínio HTTPS e só então considere o teste REAL controlado acima. Nunca execute
`docker compose down -v` durante deploy ou rollback.

## Operational complement — final validation

Validado em 2026-08-11 BRT no projeto Docker isolado
`nexustrader-phase2-ops`, porta `127.0.0.1:8993`, com API e bot, volume persistente,
`DERIV_ACCOUNT_TYPE=demo`, `ALLOW_REAL_TRADING=false` e `REAL_MAX_STAKE_USD=0`.

Evidências funcionais reais:

- gráfico R_100/M1 recebeu histórico e ticks, com Bollinger, preço, ADX e gate ao vivo;
- filtros TODAS/CHAMPION/TRIAL, journal de decisões, contratos liquidados e uma posição
  por lane foram renderizados sem F5;
- formulário do Champion restaurou a revisão 2 com Martingale, stake USD 0,50,
  multiplicador 2, três níveis, meta 20, stop 10, máximo 50 operações, stake máxima 4,
  três losses e cooldown 15 minutos;
- o Trial não apresentou controle de start, stop ou gerenciamento;
- `INICIAR CHAMPION` abriu obrigatoriamente o formulário com `SALVAR E INICIAR`; o teste
  foi cancelado antes de habilitar o Champion;
- viewport desktop e 390x844 não apresentaram overflow horizontal; ações primárias se
  reorganizaram em coluna no mobile;
- reinícios separados de API e bot preservaram snapshot version 2, versões Champion e
  Trial e revisão do gerenciamento; a interface voltou a LIVE sem F5;
- após o último rebuild/restart, o scan integral dos logs retornou zero ocorrências de
  `Traceback`, `ERROR`, `IntegrityError`, compra REAL, contrato duplicado ou ownership
  ambíguo.

Defeito adicional encontrado pelo teste real de restart: quando o runtime era criado
sem snapshot injetado e existia contrato Champion OFF/DEMO ativo, o gerenciamento
persistido era carregado tarde demais. A mudança aparente adiava a aplicação dos IDs de
versão e a primeira decisão podia violar `nexus_decisions.nexus_version_id NOT NULL`.
O caso ganhou RED, o bootstrap passou a hidratar gerenciamento antes de risco/ownership,
quatro regressões de restart passaram e o cenário Docker foi repetido sem erro.

Os limites de gerenciamento são exclusivamente humanos. O aprendizado não altera
stake, modelo de gerenciamento, meta, stop, máximo de operações, stake máxima, losses
consecutivos ou cooldown. Todos são revistos pelo usuário no formulário aberto antes
de iniciar o Champion; em OFF, Champion e Trial continuam em DEMO a USD 0,35.

## Addendum — automatic laboratory and live journal, 2026-08-12

### Current code gate

| Gate | Result |
| --- | --- |
| Python full suite | 562/562 PASS em 118,224 s; 1 teste de processo ignorado porque a sessão gerenciada negou CIM |
| Strategy and entry clock | 41/41 PASS |
| Repository/API/lab/reports/promotion/smoke | 150/150 PASS |
| JavaScript full suite | 53/53 PASS |
| `node --check static/js/app.js` | PASS |
| Python `compileall` | PASS |
| `pip check` | PASS, sem dependências quebradas |
| `docker compose config --quiet` | PASS com perfil DEMO-only |
| `git diff --check` | PASS |
| Donchian, indicators e Nexus Speed | zero diff desde a base protegida |

O teste ignorado exercita a limpeza de uma árvore de processos iniciada pelo launcher
Windows. Ele continua ativo em Windows normal; somente é ignorado quando a própria
consulta `Get-CimInstance Win32_Process` é recusada pelo sandbox. Não afeta Linux/VPS.

### Strategy binding

O contrato do Champion V1 foi novamente conferido contra `STRATEGY.md` e a suíte de
41 testes: `R_100`, M1, 58 segundos, Bollinger `20/2/SMA`, rompimentos estritos por
candle fechado, espera ilimitada pela primeira vela contrária nas bandas externas,
cruzamento central sem confirmação adicional, `ADX <= 22`, abertura seguinte e limite
absoluto de dois segundos. O gate ML somente bloqueia uma direção determinística; não
cria nem inverte uma entrada.

### Defects corrected in this complement

1. O scheduler diário agora fecha e publica o relatório diário das 10:00 BRT.
2. O fechamento semanal agora monta evidência de promoção vinculada a campanha,
   versão, artefato, configuração, dataset e proveniência antes de avaliar os gates.
3. A proposta humana é criada somente por relatório e Trial executável elegíveis; o
   Champion nunca é alterado automaticamente.
4. A rotação semanal do Trial para um SHADOW executável ocorre fora do caminho M1 e é
   idempotente; ao trocar o Trial, a meta contínua de 300 começa novamente para o novo
   candidato.
5. O smoke test esperava incorretamente somente uma campanha ativa. O contrato real é
   exatamente duas: uma `champion_baseline` e uma `challenger_trial`, ambas coerentes
   com seus ponteiros de versão.
6. O journal público misturava snapshots internos de lane e settlements com decisões
   operacionais. A consulta agora aceita somente envelopes de decisão, achata o payload
   causal e não expõe linhas internas duplicadas.
7. O launcher Windows normaliza `Path`/`PATH`; a limpeza de processos permanece
   fail-closed e testada quando CIM está disponível.

### Runtime and browser evidence

Uma API isolada, com banco novo, REAL desabilitado e sem acesso à Deriv, retornou
`PASS_READ_ONLY`: duas lanes, duas versões, duas campanhas ativas, snapshot revision 1,
zero reconnect e zero operação fabricada.

Na aplicação Docker já ativa em `127.0.0.1:8993`, o navegador real confirmou:

- console sem warning ou erro;
- parada e início do Champion com formulário de gerenciamento obrigatório;
- Fixed, Martingale, Soros, limites diários, stake máxima, losses e cooldown editáveis
  apenas para o Champion ON;
- Trial sem start, stop ou gerenciamento;
- gráfico R_100/M1 sincronizado com candles, Bollinger superior, SMA 20, Bollinger
  inferior e marcadores WIN/LOSS;
- posições liquidadas sem card infinito e campanha Trial visível em `10/300`;
- Evolution e Reports em estado vazio honesto antes do primeiro fechamento, sem botões
  humanos habilitados indevidamente.

Os valores antigos `8/7 dias` e `327/300 operações` pertenciam exclusivamente ao banco
sintético usado para validar o frontend e não são seed de produção. Bancos novos começam
com zero progresso; o acumulador real deriva apenas de settlements Trial da campanha.

### Final deployment status

**CODE GO / IMAGE REBUILD PENDING.** O pipe do daemon Docker ficou inacessível para a
sessão gerenciada durante este último complemento. Por isso, a interface visual acima é
da imagem operacional imediatamente anterior, enquanto as correções finais de
scheduler, evidência, smoke e journal foram provadas por testes e API isolada. Antes do
deploy, execute `scripts/rebuild_local_docker.ps1` em um PowerShell com acesso ao Docker
e repita o scan de logs descrito no runbook. Nenhuma ordem REAL foi autorizada.

## Addendum — regression closure after local retest, 2026-08-12

### Regressions reproduced and closed

1. `tests.test_nexus_trade_learning_lab` falhava em quatro casos por data fixa de
   boundary (`2026-08-12T13:00:00Z`) que envelheceu além de `campaign.started_at`.
   A falha era de teste, não do scheduler: o contrato fail-closed de `due_windows()`
   foi preservado e os testes passaram a derivar a primeira fronteira diária a partir
   da campanha ativa, como já ocorria nos cenários semanais.
2. `tests.test_nexus_trade_tick_archive` falhava em três casos de restart causal por
   divergência apenas textual do mesmo path no Windows. O manifesto agora canoniza
   paths com `Path.resolve()` e aceita aliases equivalentes sem relaxar a validação de
   `sha256`, bytes, sequência ou conteúdo.

### Current retest gate

| Gate | Result |
| --- | --- |
| `tests.test_nexus_trade_learning_lab` | 19/19 PASS |
| `tests.test_nexus_trade_tick_archive` | 16/16 PASS |
| Python focal `test_nexus_trade*.py` | 348/348 PASS em 150,556 s |
| Python `compileall` | PASS |
| `pip check` | PASS |
| `git diff --check` | PASS |
| `docker compose config --quiet` | não executado nesta sessão, porque `docker` não estava disponível no PATH da sessão gerenciada |

### Local runtime and browser evidence

- `scripts/start_dev.ps1` iniciou com `STARTED_SAFE`, `account_type=demo` e
  `allow_real_trading=false`.
- `GET http://127.0.0.1:8990/api/v1/health/live` retornou `{\"status\":\"alive\"}`.
- O navegador real autenticou no dashboard local, carregou a linha fixa do
  `NexusTrade R_100 · 1m OFF — APRENDENDO EM DEMO` e abriu a aba `OPERAÇÃO`.
- A UI exibiu `VER EVOLUÇÃO`, `GERENCIAMENTO`, `PARADA TOTAL`, `INICIAR CHAMPION`,
  abas `OPERAÇÃO/RELATÓRIOS/EVOLUÇÃO`, cards `Champion V1` e `Trial V1`, além da área
  `Gráfico operacional`.
- Console do navegador: sem mensagens.
- Rede do navegador: carregamento bem-sucedido dos módulos `nexus_trade_*`,
  `POST /api/v1/ws-tickets/nexus-trade` e `GET /api/v1/nexus-trade`.
- Log do launcher local: sem `Traceback`; inicialização explícita em DEMO-only.

### Status atualizado

**CODE GO local mantido.** O fechamento focal hoje ficou reproduzível no workspace
atual. Antes do próximo deploy, ainda é necessário validar `docker compose config --quiet`
e rebuild da imagem em uma sessão que tenha `docker` disponível no PATH.

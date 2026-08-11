# NexusTrade Phase 2 — Validation record

## Decision

**GO** em 2026-08-11 09:30 BRT para integrar a interface NexusTrade ao ambiente de
homologação. Nenhuma ordem REAL foi autorizada ou enviada durante a Fase 2.

Branch local: `feature/nexustrade-frontend`  
Base validada da Fase 1: `9b3232290c22a0d878e06ad6df3c8db465e57662`

## Automated evidence

| Gate | Result |
| --- | --- |
| Python full suite | 502/502 PASS em 49,711 s |
| JavaScript full suite | 42/42 PASS |
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


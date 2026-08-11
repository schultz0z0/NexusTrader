# NexusTrade Phase 1 — validação local, Docker e DEMO

## Decisão

**GO para a Fase 2**, com os incidentes encontrados nesta execução corrigidos e
revalidados. O gate final Docker confirmou persistência, restart separado da API e do
bot, correlação decisão–trade, stake DEMO fixa e logs estruturais sem dados sensíveis.

- Evidência finalizada em `2026-08-11T04:16:20.788Z`
  (`2026-08-11T01:16:20.793-03:00`, America/Sao_Paulo).
- Commit-base validado: `fbd3d74da56c88c287d7e4ef05536688d228bc33`.
- Python `3.11.15`, Node `24.16.0`, Docker `29.5.3`, Compose `5.1.4`.
- Nenhum token, login, account ID, balance, ticket, contract ID ou path local é
  registrado neste documento.

## Contrato de segurança aplicado

Toda inicialização local e Compose aplicou, depois do arquivo de ambiente:

- bind `127.0.0.1` e projeto/porta/DB/log/PID exclusivos;
- `DERIV_ACCOUNT_TYPE=demo`;
- `ALLOW_REAL_TRADING=false`;
- limite REAL igual a zero;
- human action key dummy distinta das demais autoridades;
- NexusTrade `R_100`, M1, duração `58s`, stake DEMO exata `USD 0.35`;
- Champion `OFF/DEMO` e Trial DEMO;
- smoke somente `GET`, sem proposal, compra explícita, mudança de modo ou sinal
  forçado.

O arquivo de ambiente original foi usado somente como input em memória. O worktree
não recebeu cópia dele. Bancos, logs, PID files e resultados brutos foram removidos
depois da extração sanitizada; o volume Docker isolado foi preservado e o projeto foi
encerrado sem `-v`.

## TDD e regressão

| Gate | Resultado | Duração |
|---|---:|---:|
| Smoke CLI offline, RED inicial | 6 falhas esperadas | — |
| Smoke CLI offline, primeiro GREEN | 7/7 | < 1s |
| PID/cleanup do launcher | 3/3 | 8.225s |
| Redação de auth/connection/smoke | 10/10 | 0.207s |
| Dual cutoff causal, RED | 0/2; 2 errors esperados | 0.002s |
| Dual cutoff causal, GREEN | 3/3 | 0.060s |
| Indicadores/runtime/clock | 74/74 | 0.772s |
| Suíte completa após dual cutoff | 449/449 | 48.903s |
| Suíte completa final da árvore a commitar | 452/452 | 47.184s |
| JavaScript final | 17/17 | 92.265ms |
| `compileall` | exit 0 | — |
| `pip check` | sem requisitos quebrados | — |

A verificação final, executada depois do ajuste do parser do smoke e da documentação,
é o gate canônico desta entrega e deve permanecer com zero falhas.

## Localhost

O launcher isolado validou health, autenticação, singleton, contratos de API,
relatórios/exports, restart separado e persistência. O smoke read-only retornou
`PASS_READ_ONLY` com 2 lanes, 1 campanha, 1 versão, 0 relatórios, 0 exports e 0
reconexões. A janela M1 autorizada confirmou DEMO por REST, WebSocket, histórico de
20 pontos e tick; não houve tentativa REAL.

Uma execução com credencial dummy produziu 58 recusas de Trial em 585.759s
(`5.94/min`). Elas foram classificadas como `ENV_EXPECTED`, com warning pela ausência
de backoff explícito no orquestrador. A assinatura não apareceu no gate DEMO final.

## Docker e restart

O Compose foi renderizado com 2 serviços, ambos read-only, `cap_drop: ALL`, bind
loopback, conta DEMO, REAL desabilitado e limite REAL zero. A remoção de
`container_name` permitiu projeto, containers e volume exclusivos.

- Build: 2 imagens produzidas. O wrapper observou 39.8s; uma linha de progresso em
  stderr foi elevada pelo PowerShell e registrada como warning de harness, não como
  falha da imagem.
- Primeiro `up`: API healthy e bot running em 6.823s, restart count 0/0.
- Observação GET-only: 66.245s, `PASS_READ_ONLY`, 2 lanes, 1 campanha, 1 versão e 0
  reconexões.
- O primeiro resultado exibiu `approved_signals=0` apesar de 2 novos contratos. A
  análise read-only do volume provou 1 decisão aprovada e 1 trade por lane, correlação
  1:1, stake `.35`, delay dentro de 2s e zero órfãos. O smoke lia o envelope persistido
  como formato plano; o parser foi corrigido por TDD para `payload.decision`, exige
  `blocked_reason is null`, stake `.35` e `decision_id` correlacionado.
- Restart-only final no mesmo volume/imagens: `up` em 6.441s; API restart + smoke em
  7.791s; bot restart e reconciliação bounded em 17.250s.
- Bot worker: PID mudou de `46873` para `54727`.
- Ponteiros de versões/campanha/modo: hash antes/depois
  `85ea3d96aab0b064d7ddd319e680fd38ddf47cd3c3c0ec151098a93376595e57`.
- Snapshot version permaneceu `1`; decisões avançaram `14 → 18 → 32` sem regressão
  de ponteiro.
- Trades avançaram `2 → 2 → 4`; 4 contratos únicos, 4/4 decisões aprovadas e
  correlacionadas, stake `.35` em 4/4, duplicatas 0, reconexões 0.
- `down` final: 2.362s, containers 0, listeners 0, volume isolado preservado.

## Scan estruturado final

Nos logs Docker e outputs públicos finais, cada contador abaixo foi `0`:

- valores exatos de tokens/chaves;
- account IDs por padrão estrito e account ID configurado por comparação exata;
- `REAL`, `ERROR`, `Traceback`, `dual_cutoff`;
- `duplicate`, `ambiguous`, falha de hash/fingerprint;
- entry delay acima de 2s.

Uma regex preliminar permissiva contou 9 falsos positivos em palavras de progresso do
Compose. O re-scan canônico exige prefixo de conta seguido de dígitos e também compara
o valor configurado em memória; ambos retornaram zero, inclusive nos logs de runtime.

## Incidentes corrigidos

1. O primeiro probe externo permitiu que logs legados de auth exibissem identidade e
   balance. A execução foi encerrada, os artefatos sensíveis foram removidos e
   `AuthManager`, conexão e probes passaram a registrar apenas status, contagens e
   tipos de erro. Testes com sentinelas impedem regressão.
2. O redirector do virtualenv podia deixar o worker real fora do PID tracking. O
   launcher passou a persistir/verificar root e worker, encerrar apenas descendentes
   comprovados e redirecionar todos os pipes. O teste focused confirmou cleanup.
3. O runtime fornecia simultaneamente `decision_epoch` e `active_candle_time`. Os
   chamadores agora preferem o instante de decisão e usam o bucket ativo somente como
   fallback; o seletor causal de baixo nível continua rejeitando ambiguidade.
4. O probe host da primeira janela Docker falhou por configuração incompleta do
   próprio harness e não foi repetido. O restart final omitiu esse probe, validou o
   runtime Docker diretamente e manteve o scan limpo.
5. Um comparador offline tentou ler saída UTF-16 como UTF-8. Os snapshots já capturados
   foram comparados sem nova requisição usando detecção de BOM; o resultado foi
   `PASS_RESTART`.

## Comandos repetíveis

```powershell
python -m compileall -q api backtest core data database nexus_trade risk strategies trading
python -m unittest discover -s tests -v
node --test tests/js/*.test.mjs
python -m pip check

powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1 -PreflightOnly -EnvFile <env-local>
python -m scripts.nexus_trade_smoke --base-url http://127.0.0.1:<porta> --demo-only

docker compose -p <projeto-isolado> config --quiet
docker compose -p <projeto-isolado> up -d --build
docker compose -p <projeto-isolado> restart nexus-api
docker compose -p <projeto-isolado> restart nexus-bot
docker compose -p <projeto-isolado> down
```

Para Compose, force no ambiente do comando todos os overrides de segurança descritos
acima. Nunca use `down -v` sobre o volume de evidência e nunca reutilize projeto/porta
de outro processo.

# NexusTrader — desenho final de prontidão operacional

Data: 2026-08-07

## Objetivo

Fechar as pendências técnicas conhecidas sem executar ordens reais durante a
validação. O resultado deve operar em DEMO local, permitir que o operador selecione
uma conta REAL somente quando a VPS estiver explicitamente liberada e oferecer
evidências reproduzíveis de estratégia, persistência, recuperação, saúde e deploy.

## Perfil imutável da estratégia

- Donchian: maior `high` e menor `low` das últimas 21 velas, incluindo a vela viva.
- ZigZag causal: `deviation=1%`, `depth=15`, `backstep=3`.
- Feed: candles de 60 segundos construídos apenas com ticks presentes/passados.
- Expiração: 2 minutos.
- PUT: a ponta flutuante atual do ZigZag é `high`, pertence à vela viva e seu valor
  alcança ou supera o Donchian superior da mesma vela.
- CALL: a ponta flutuante atual do ZigZag é `low`, pertence à vela viva e seu valor
  alcança ou fica abaixo do Donchian inferior da mesma vela.
- Um mesmo extremo/vela só pode gerar um sinal. Nenhum dado futuro confirma ou
  altera retroativamente uma decisão já emitida.

O ZigZag de referência que confirma pivôs com barras futuras é adequado para análise
histórica, mas não pode ser usado diretamente para uma ordem ao vivo sem look-ahead.
A ponta flutuante causal preserva a intenção visual dos anexos e sua natureza de
repintura é explicitamente testada e documentada.

## Ownership de ordens

Cada compra passa por um journal `order_intents` antes da transmissão. O estado segue
`prepared -> submitting -> owned`; respostas de transporte ambíguas seguem para
`reconcile_pending`, nunca para retry de `buy`. Há no máximo um intent não resolvido
por conta, serializando a janela de compra entre robôs da mesma conta.

Uma assinatura de transações é aberta antes da compra. Em reconnect/restart, o
sistema consulta `portfolio` e `statement`, compara conta, janela de tempo, valor,
símbolo e tipo esperados e assume ownership somente quando existe um único candidato.
Zero ou múltiplos candidatos mantêm a conta em quarentena/fail-closed e aparecem na
API. Uma resolução explícita pode vincular ou cancelar um intent depois de auditoria;
ela não dispara nova compra.

## Estado de risco

Martingale, Soros e circuit breaker passam a ter snapshot persistente por bot. O
fechamento de trade e o avanço do snapshot são gravados na mesma transação SQLite e
marcados por `risk_applied`, tornando callbacks repetidos idempotentes. Restart carrega
stake, nível, sequências e cooldown exatamente do último settlement confirmado.

## Saúde e recuperação

- `/api/v1/health/live`: processo HTTP responsivo.
- `/api/v1/health/ready`: SQLite e, para cada bot desejado como RUNNING, heartbeat,
  conexão Deriv, publisher e feed recentes.
- `/api/v1/health`: alias compatível da readiness.
- Bots STOPPED não derrubam readiness.
- O orquestrador normaliza telemetria antiga `STARTING/RUNNING/STOPPING` para `STOPPED`
  quando não há sessão local e a intenção persistida é STOPPED.

## Liberação de conta REAL

REAL exige simultaneamente:

1. `ALLOW_REAL_TRADING=true` no servidor;
2. `REAL_MAX_STAKE_USD` positivo e stake do bot dentro desse teto;
3. conta ativa retornada pela Deriv e tipo/ID iguais aos persistidos;
4. ticket curto, de uso único, emitido pelo backend após o usuário digitar
   `REAL <account_id>` e vinculado ao bot, conta e `config_revision` atuais.

O endpoint de start consome o ticket. Alterar configuração invalida a confirmação.
O ambiente local continua forçando DEMO. Testes de REAL validam apenas os bloqueios e
o ticket; nunca chamam proposta ou compra.

## Replay, backtest e paper

O replay consome JSONL de ticks em ordem estrita, constrói candles de 1 minuto com o
mesmo agregador causal, instancia a mesma estratégia e usa um broker em memória. Uma
posição vence ou perde pelo primeiro tick em/depois do expiry; ausência desse tick
torna o run `INCOMPLETE`, sem resultado fabricado. Dataset, parâmetros, versão e seed
são registrados com SHA-256.

O relatório inclui trades válidos/inválidos, P&L, win rate, expectancy, profit factor,
payoff, drawdown absoluto/percentual/duração, loss streak, exposição, distribuição de
payout e equity curve. Métrica nenhuma autoriza REAL automaticamente: aprovação de
capital continua humana.

## Fronteira interna e containers

O token interno usa comparação constante. O ingress Traefik exclui
`/api/v1/internal`; somente a rede Docker acessa o endpoint. A imagem roda como usuário
sem privilégios, com filesystem somente leitura onde possível, `tmpfs`, volume apenas
para SQLite, limites de CPU/memória/PIDs, rotação de logs, healthcheck e shutdown longo
para settlement. Backup, migração, rollback e smoke DEMO antecedem qualquer liberação
REAL.

## Critérios de aceite

- Testes RED/GREEN cobrem todas as regras acima.
- Suite Python/JS, `compileall`, `pip check` e `docker compose config` passam.
- Build e health dos containers passam quando o Docker local estiver disponível.
- Browser real valida seleção DEMO/REAL, confirmação REAL sem start financeiro,
  configuração fixa, gráfico/snapshot, start/stop DEMO e console.
- Toda limitação de validação externa é registrada; “100%” não é usado como promessa
  de lucro, ausência de risco ou disponibilidade da Deriv.

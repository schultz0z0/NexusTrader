# NexusTrade — recuperação operacional e laboratório automático

Status: **aprovado em 2026-08-11**

## 1. Objetivo

Fechar os desvios encontrados na validação real do NexusTrade: retomar contratos após
restart, refletir o progresso verdadeiro da campanha Trial, executar o laboratório de
aprendizado sem intervenção diária e tornar Bollinger 20/2/SMA visível no gráfico M1.

Este adendo complementa `PRD.md`, `STRATEGY.md`, `ML-GOVERNANCE.md`,
`PROMOTION-GATES.md` e o complemento operacional da Fase 2. Em caso de dúvida, as
invariantes desses documentos continuam prevalecendo.

## 2. Recuperação de contratos

No bootstrap, toda lane não-IDLE deve recuperar a identidade durável da conta, o
dispatcher e o monitor exatos antes da reconciliação. O Champion OFF usa o mesmo
monitor DEMO compartilhado do Trial. Depois dessa associação, um contrato ACTIVE é
consultado novamente na Deriv e:

- se ainda estiver aberto, volta a publicar `nexus.position`;
- se estiver vendido, a liquidação é persistida atomicamente e a lane volta a IDLE;
- se o resultado for desconhecido, permanece em quarentena/reconciliação, sem liberar
  outra compra nem exigir edição manual do banco.

O frontend só libera gerenciamento e mudança de modo depois do snapshot persistido
confirmar Champion IDLE.

## 3. Progresso da campanha

O progresso é calculado a partir de trades `challenger_trial` liquidados e vinculados
ao ID exato da campanha Trial ativa. O snapshot expõe `progress.completed` e
`progress.target=300`. O frontend seleciona explicitamente a campanha Trial, nunca a
primeira campanha ativa, e atualiza o valor por eventos idempotentes até a próxima
hidratação. Restart e reconnect recalculam o valor pelo banco.

A semana visual continua segunda 10:00–segunda 10:00 em Brasília. O acumulador 300 só
zera quando uma decisão humana encerra a campanha ou quando a rotação automática cria
um novo Trial, conforme o PRD.

## 4. Laboratório automático

Um serviço interno independente do caminho crítico de compra executa trabalho vencido
por fronteiras persistidas, sem `sleep` usado como fonte de verdade:

1. diariamente às 10:00 de Brasília, fecha o relatório devido e tenta construir um
   dataset causal exclusivamente de oportunidades com features anteriores à entrada e
   resultados já liquidados;
2. treina de forma determinística o gate `OPERAR/NÃO OPERAR`, registra sucesso,
   rejeição ou falha e cria candidatos `SHADOW` content-addressed;
3. compara ablações e famílias autorizadas do repertório. Bollinger nunca é removida e
   direção continua pertencendo às regras determinísticas;
4. na segunda-feira às 10:00, fecha a semana e permite ao serviço de promoção manter
   o Trial atual ou substituí-lo pelo melhor SHADOW elegível;
5. nenhuma rotina automática promove, altera ou sobrescreve o Champion.

O Trial permanece congelado durante sua campanha. Resultados novos geram SHADOWs para
a próxima campanha. Falta de amostra, feature causal incompleta, uma única classe,
artefato inválido ou qualquer inconsistência produz resultado explícito fail-closed,
sem candidato operacional.

### 4.1 Exploração autorizada

O primeiro gerador automático usa um change budget de uma família material por
candidato. Ele pode testar subconjuntos/configurações do repertório já calculado:
Bollinger features, ADX/DMI, CHOP, ATR/ATRP, RSI, Stochastic, CCI, Keltner, ROC, Aroon,
SMA, EMA, WMA, HMA, KAMA e características do candle. Inclusão ou remoção só é
qualificada com ablação vinculada ao dataset e melhora fora da amostra. Bollinger
20/2/SMA continua a configuração operacional V1 e permanece obrigatória em todo
artefato executável; variações de sua configuração só podem aparecer como candidato
documentado quando todo o runtime suportar o snapshot correspondente.

## 5. Observabilidade e frontend

O snapshot e eventos mostram tentativa de treino, estado do laboratório, candidato,
hash, dataset, features testadas, ablações e motivo de manter/trocar Trial. Nenhuma
transição fica silenciosa. Relatórios diários/semanais continuam sendo a evidência de
governança; assertividade é exibida, mas não decide sozinha.

O gráfico operacional usa o feed R_100/M1 existente e desenha três linhas distintas:
Bollinger superior, SMA central e Bollinger inferior, calculadas com período 20,
desvio 2 e SMA. Linhas ficam visíveis junto aos candles e não dependem de hover.

## 6. Segurança e falhas

- Desenvolvimento e validação usam DEMO, `ALLOW_REAL_TRADING=false` e teto REAL zero.
- Treino/relatório nunca bloqueiam a fronteira M1 nem repetem compra.
- Jobs são idempotentes, persistidos e recuperáveis após restart.
- Uma falha de aprendizado não interrompe Champion/Trial congelados.
- Segredos, contas completas, tokens, tickets e caminhos locais não entram em eventos,
  DOM, relatórios ou logs.
- Donchian e Nexus Speed permanecem sem alteração comportamental.

## 7. Aceite

O trabalho recebe GO somente quando testes e Docker/navegador real provarem:

- o contrato atualmente preso é reconciliado e liquidado após restart;
- gerenciamento e início do Champion voltam a funcionar quando a lane fica IDLE;
- campanha Trial mostra a contagem real e progride sem F5;
- jobs diários/semanais são idempotentes e sobrevivem a restart;
- uma tentativa automática aparece no ledger e no frontend, sem promover Champion;
- Bollinger 20/2/SMA aparece visualmente no gráfico;
- banco, WebSocket, rede/console e logs de API/bot não apresentam traceback, loop,
  contrato órfão, duplicidade ou vazamento.

# Estratégia NexusTrade Champion V1

Status: **rascunho; regras confirmadas e questões abertas estão identificadas**

## 1. Dados causais

- O único ativo aceito é `R_100`.
- Todas as decisões de setup usam somente candles M1 fechados.
- A vela viva pode ser exibida e coletada, mas não confirma rompimento ou reversão.
- Indicadores são calculados pela biblioteca `stock_indicators` sobre OHLC M1 de
  frequência consistente.
- Nenhum dado futuro pode alterar uma decisão passada.

## 2. Indicadores obrigatórios

### 2.1 Bollinger Bands

- Período V1: `20`.
- Desvios V1: `2`.
- Linha central V1: `SMA` do fechamento.
- Indicador primário e obrigatório em todo Champion.
- O Challenger pode testar período e desvio, mas não remover Bollinger.

A implementação de referência é
[`get_bollinger_bands(quotes, 20, 2)`](https://python.stockindicators.dev/indicators/BollingerBands/).
A função retorna `sma`, `upper_band`, `lower_band`, `%B`, `z_score` e `width`.

### 2.2 ADX

- Período V1: `14`.
- Calculado continuamente sobre candles M1 fechados.
- Não bloqueia reconhecimento de rompimento, espera ou confirmação.
- É verificado como última porta de autorização imediatamente antes da compra.
- `ADX <= 22` permite a entrada.
- `ADX > 22` bloqueia a ordem.
- Uma entrada bloqueada por ADX é registrada e consumida; a lane volta a `IDLE` e
  aguarda um novo sinal completo.
- A oportunidade e seu resultado hipotético continuam registrados para o Challenger.
- A biblioteca recomenda histórico adicional para convergência; a V1 deve carregar
  pelo menos 278 candles anteriores ao ponto de decisão sempre que possível.

Referência: [`get_adx(quotes, 14)`](https://python.stockindicators.dev/indicators/Adx/).

## 3. Regras de rompimento

Um pavio isolado não confirma rompimento. A confirmação exige candle M1 fechado.

### 3.1 Banda superior

1. Um candle fecha acima de `upper_band`.
2. A banda superior deve estar inclinada para cima, definido estritamente por
   `upper_band[t] > upper_band[t-1]`.
3. O estado passa a `WAITING_BEARISH_CONFIRMATION`.
4. Podem existir quantos candles de alta forem necessários; não há timeout por
   quantidade de candles.
5. Doji não é confirmação.
6. O primeiro candle com `close < open` confirma reversão, mesmo que feche fora das
   bandas.
7. A entrada é `PUT` na abertura do M1 imediatamente seguinte.
8. A duração é 58 segundos.

### 3.2 Banda inferior

1. Um candle fecha abaixo de `lower_band`.
2. A banda inferior deve estar inclinada para baixo, definido estritamente por
   `lower_band[t] < lower_band[t-1]`.
3. O estado passa a `WAITING_BULLISH_CONFIRMATION`.
4. Podem existir quantos candles de baixa forem necessários; não há timeout por
   quantidade de candles.
5. Doji não é confirmação.
6. O primeiro candle com `close > open` confirma reversão, mesmo que feche fora das
   bandas.
7. A entrada é `CALL` na abertura do M1 imediatamente seguinte.
8. A duração é 58 segundos.

### 3.3 Linha central

- Rompimento para cima: candle abre abaixo da SMA e fecha acima dela.
- Rompimento para baixo: candle abre acima da SMA e fecha abaixo dela.
- Toque ou pavio sem fechamento do outro lado não confirma.
- Não há candle adicional de confirmação.
- Assim que o candle fechado atravessa a SMA, o sinal está completo; nenhuma outra
  condição de confirmação de preço pode atrasá-lo.
- Rompimento para cima gera `CALL` na abertura do próximo M1.
- Rompimento para baixo gera `PUT` na abertura do próximo M1.
- A duração é 58 segundos.

## 4. Máquina de estados proposta

```text
WARMUP
  -> IDLE

IDLE
  -> WAITING_BEARISH_CONFIRMATION  (fechamento acima da banda superior)
  -> WAITING_BULLISH_CONFIRMATION  (fechamento abaixo da banda inferior)
  -> ENTRY_PENDING_CALL            (rompimento central para cima)
  -> ENTRY_PENDING_PUT             (rompimento central para baixo)

WAITING_BEARISH_CONFIRMATION
  -> permanece                     (candle de alta ou doji)
  -> ENTRY_PENDING_PUT             (primeiro candle de baixa)

WAITING_BULLISH_CONFIRMATION
  -> permanece                     (candle de baixa ou doji)
  -> ENTRY_PENDING_CALL            (primeiro candle de alta)

ENTRY_PENDING_CALL / ENTRY_PENDING_PUT
  -> ENTRY_BLOCKED_ADX             (ADX fora do limite)
  -> ENTRY_BLOCKED_STALE           (abertura perdida)
  -> CONTRACT_OPEN                 (compra confirmada)
  -> OWNERSHIP_QUARANTINE          (resultado da compra desconhecido)

CONTRACT_OPEN
  -> AWAITING_SETTLEMENT
  -> IDLE                          (liquidação confirmada)

ENTRY_BLOCKED_ADX
  -> IDLE                          (sinal consumido sem ordem)
```

O caminho operacional pode ser bloqueado pelo ADX, mas um caminho observacional
paralelo sempre registra características, previsão, decisão e resultado hipotético.

### 4.1 Deduplicação e concorrência

- Cada lane (`champion_baseline` ou `challenger_trial`) pode possuir no máximo um
  contrato ativo.
- Champion e Trial Challenger podem possuir um contrato cada ao mesmo tempo, pois são
  lanes independentes usadas na comparação.
- Se duas ou mais regras da mesma lane confirmarem a mesma direção na mesma abertura,
  somente um contrato é comprado.
- O sinal persiste todos os `reason_codes` aplicáveis; por exemplo,
  `upper_reversal_confirmation` e `center_cross_down` para uma única entrada `PUT`.
- O setup pendente mais antigo é a causa primária para auditoria, sem apagar as causas
  adicionais.
- Se a lane ainda possuir contrato não liquidado, uma nova entrada é bloqueada; nunca
  existem dois contratos simultâneos dentro da mesma lane.

## 5. Entrada na abertura

- O sinal nasce no fechamento do candle confirmador.
- A virada temporal do M1 dispara a ordem na abertura do candle seguinte; o executor
  não espera o primeiro tick do novo candle.
- O candle anterior é finalizado causalmente na fronteira do minuto usando o último
  tick pertencente ao bucket encerrado.
- A duração solicitada é 58 segundos para absorver parte da latência.
- O limite absoluto é 2 segundos contados desde o epoch de abertura do novo M1.
- Depois de 2 segundos, a entrada fica stale e não pode ser perseguida no meio do
  candle.
- Não existe retry cego de compra.
- A meta operacional é que a compra seja aceita dentro do primeiro segundo do novo M1.
- Uma compra aceita entre 1 e 2 segundos é permitida como contingência operacional.
- A meta de engenharia é despachar a solicitação antes de 250 ms da virada: conexão
  WebSocket aquecida, relógio monitorado, indicadores pré-calculados, estado em memória
  e treinamento fora do processo crítico de execução.
- O primeiro tick do novo candle atualiza mercado e auditoria, mas não é pré-requisito
  para disparar a ordem, pois aguardar esse tick pode piorar o preço de entrada.
- Propostas de Champion e Trial Challenger são solicitadas concorrentemente na virada;
  as compras na conta DEMO compartilhada são correlacionadas e serializadas sem
  ultrapassar o limite de 2 segundos.

## 6. Empates e valores na linha

- `open == close` é doji e não confirma reversão externa.
- Fechamento exatamente igual à banda externa não é rompimento.
- Abertura ou fechamento exatamente igual à SMA não satisfaz o cruzamento estrito da
  linha central.

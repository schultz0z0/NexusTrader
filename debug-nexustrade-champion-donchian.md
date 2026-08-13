# [OPEN] Debug Session: nexustrade-champion-donchian

## Contexto

- Sintoma 1: Champion do NexusTrade nao inicia corretamente e fica em estado de
  liquidacao/reconciliacao persistente.
- Sintoma 2: o reset do gerenciamento por sessao pode nao estar sendo refletido ao
  iniciar o Champion.
- Sintoma 3: o robo comum Donchian, em conta real, teria passado a janela entre
  00:00 e 08:00 de 13/08 sem abrir operacoes.

## Hipoteses iniciais

1. O Champion ficou preso em `reconcile_pending` por uma compra ambigua e isso impede
   tanto o retorno para `IDLE` quanto um novo inicio limpo.
2. O backend do Champion esta em estado preso, mas a UX do gerenciamento por sessao
   tambem nao rehidratou corretamente o snapshot ao reiniciar.
3. O gerenciamento da sessao do Champion nao foi resetado semanticamente na transicao
   OFF -> ON, e o card esta exibindo configuracao ativa anterior.
4. O Donchian real nao ficou parado por falha estrutural; ele pode ter gerado sinais,
   proposals ou trades fora do recorte inicialmente observado.
5. O Donchian real pode ter ficado sem operar nessa janela por conta/estado/runtime
   especifico diferente do bot que aparece nos logs agregados.

## Evidencias coletadas

- `nexus_lane_heads`: Champion em `QUARANTINED`, Trial em `IDLE`.
- `order_intents`: Champion com `reconcile_pending` e erro
  `buy response did not establish rejection or ownership`.
- `nexus-bot` mostra compras/encerramentos recentes do NexusTrade e do Donchian em
  outros recortes do dia.

## Proximos passos

1. Minerar `log.md` no intervalo 00:00-08:00 de 13/08 para o Donchian real.
2. Inspecionar o `decision_id` e a reconciliacao ambigua do Champion.
3. Cruzar a semantica de gerenciamento da sessao com o runtime/snapshot atual antes de
   propor qualquer correcao.

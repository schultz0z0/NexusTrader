# NexusTrade Live Position Card Design

Data: 2026-08-12

## Objetivo

Melhorar a leitura da operacao ao vivo no workspace NexusTrade sem alterar contratos
de backend nem adicionar um fluxo visual paralelo.

## Escopo aprovado

Opcao 1, simples:

- mostrar no card vivo por lane:
  - stake
  - entrada
  - spot atual
  - P&L atual
  - status
  - compra
  - expiracao
  - rotulo textual `GANHANDO`, `PERDENDO` ou `EMPATANDO`

## Abordagem

- reutilizar a posicao viva ja existente no store e no evento `nexus.position`
- concentrar a apresentacao em helper frontend testavel
- manter `--` para campos ausentes, sem inferir valores inexistentes
- nao alterar API, WebSocket, schema ou runtime

## Validacao planejada

- teste JS RED primeiro para o helper de apresentacao
- implementacao minima no frontend
- regressao JS focada e checagem de sintaxe

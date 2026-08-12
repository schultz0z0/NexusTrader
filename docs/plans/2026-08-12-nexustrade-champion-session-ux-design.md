# NexusTrade Champion Session UX Design

## Contexto

O Champion do NexusTrade continua participando do ecossistema de comparacao com o Trial
mesmo quando esta OFF. Em OFF, ele deve permanecer no papel de baseline/laboratorio em
DEMO com stake minima, sem expor ao operador a impressao de que existe uma sessao de
gerenciamento ativa. Em ON, o Champion entra em uma sessao operacional separada, usando
o gerenciamento configurado pelo usuario apenas para essa janela operacional.

## Decisoes aprovadas

1. O gerenciamento continua persistido como sugestao operacional do Champion, mas a UX
   deve trata-lo como:
   - `gerenciamento da sessao` quando o Champion estiver ON;
   - `sugestao para a proxima sessao` quando o Champion estiver OFF.
2. O estado OFF do Champion continua exibindo DEMO fixo em `US$ 0,35`, sem misturar a
   sugestao de gerenciamento com a baseline usada para treino/comparacao.
3. A assertividade da ultima hora sera apenas informativa, dinamica e calculada somente
   para a lane `champion_baseline`.
4. Nenhum dado persistido de comparacao/aprendizado com o Trial sera resetado por causa
   do toggle ON/OFF do Champion.

## Contrato de backend

O snapshot operacional do NexusTrade passara a expor dois blocos novos:

- `champion_session`:
  - informa se existe `management_active`;
  - descreve a baseline OFF (`demo` + `0.35`);
  - expõe a configuracao persistida como `suggestion`;
  - expõe a configuracao efetivamente aplicada como `active_management` somente quando
    o Champion estiver ON.
- `champion_last_hour`:
  - janela de 60 minutos;
  - total de trades fechados do Champion;
  - wins, losses, ties e decisivas;
  - `accuracy` numerica para consumo da UI;
  - `updated_at_epoch` para telemetria/debug.

## Contrato de frontend

O card do Champion passa a mostrar:

- modo atual do Champion;
- conta atual;
- baseline OFF;
- gerenciamento da sessao ou sugestao da proxima sessao;
- assertividade da ultima hora do Champion;
- mensagem explicita de que OFF nao usa o gerenciamento, mas a sugestao fica lembrada.

O modal de gerenciamento continua sendo a fonte de configuracao do proximo ON, mas o
resumo do card muda conforme o estado ON/OFF para evitar ambiguidade operacional.

## Riscos controlados

- Nao alterar estrategia nem gatilhos de entrada.
- Nao alterar o modelo de persistencia do Trial/campanhas.
- Nao bloquear o funcionamento atual do Champion ON.
- Nao reintroduzir estado stale no frontend; a tela continua dependendo do snapshot
  como fonte de verdade.

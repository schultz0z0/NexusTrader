# Champion Session UX

## Objetivo

Registrar o comportamento aprovado para o card operacional do Champion sem alterar a
persistencia de comparacao/aprendizado do NexusTrade Learning.

## Contrato aprovado

### 1. Champion OFF

- Continua participando da baseline de comparacao em DEMO.
- Continua exibindo stake base de `US$ 0,35`.
- Nao deve parecer que existe gerenciamento de banca ativo.
- A configuracao persistida vira apenas uma `sugestao para a proxima sessao`.

### 2. Champion ON

- Passa a operar com a configuracao salva no modal de gerenciamento.
- O card deve identificar explicitamente esse estado como `gerenciamento da sessao`.
- A conta operacional e o gerenciamento da sessao devem ficar visiveis no resumo do
  Champion.

### 3. Trial

- Continua invisivel ao controle humano.
- Continua em DEMO e em stake minima para treino/comparacao.
- Nao herda, nao consome e nao mistura o gerenciamento do Champion ON.

### 4. Assertividade da ultima hora

- E apenas informativa.
- Deve ser dinamica.
- Deve considerar somente trades fechados da lane `champion_baseline`.
- Nao atua como gate de risco nem altera a estrategia.

### 5. Quarentena de ownership

- Se uma compra do Champion ficar ambigua (`buy` sem ownership comprovado), a lane nao
  pode voltar a operar por adivinhacao.
- O frontend deve diferenciar `QUARANTINED` de mera `liquidacao`, para deixar claro que
  existe um journal pendente de reconciliacao.
- Se a reconciliacao nao encontrar contrato apos a janela de espera do servidor, o
  journal pode ser fechado como `PURCHASE_ABSENT` para liberar a lane com rastreabilidade.
- Bots comuns que compartilham a mesma conta precisam bloquear novas proposals assim que
  existir `order_intent` unresolved de outro robo nessa conta.

## Notas de implementacao

- O backend pode derivar os novos blocos diretamente do snapshot duravel e do journal de
  trades, sem mudar a estrategia.
- O frontend deve continuar tratando o snapshot como fonte de verdade.
- O toggle OFF nao pode apagar historico de campanha, versoes, trades, relatorios ou
  comparativos do Learning.

## Status da implementacao

- `champion_session` foi adicionado ao snapshot operacional para separar:
  - baseline OFF em DEMO `0.35`;
  - sugestao persistida para o proximo ON;
  - gerenciamento ativo apenas durante a sessao ON.
- `champion_last_hour` foi adicionado ao snapshot operacional com wins, losses, ties,
  decisivas e accuracy apenas da lane `champion_baseline`.
- A store do frontend passou a hidratar esses dois blocos de forma explicita.
- O card do Champion agora mostra:
  - baseline OFF;
  - sugestao da proxima sessao ou gerenciamento da sessao;
  - assertividade da ultima hora do Champion.
- A sessao ON do Champion agora copia a configuracao sugerida para um estado separado de
  `sessao ativa`; ao desligar, a sessao ativa deixa de valer e o proximo ON reabre a
  partir da sugestao persistida mais recente.
- A UI do Champion agora mostra `QUARENTENA DE OWNERSHIP` quando a lane esta
  `QUARANTINED`, em vez de rotular esse estado como `AGUARDANDO LIQUIDACAO`.
- O loop dos robos comuns agora verifica `order_intents` unresolved por `account_id`
  antes de pedir nova proposal, evitando o ciclo silencioso de proposal sem compra
  quando outra lane da mesma conta ja esta em quarentena.

## Validacao prevista

- `tests.test_nexus_trade_repository`
- `tests.test_nexus_trade_api`
- `tests/js/nexus_trade_store.test.mjs`
- `tests/js/nexus_trade_view.test.mjs`

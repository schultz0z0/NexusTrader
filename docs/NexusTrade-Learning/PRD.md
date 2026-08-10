# PRD — NexusTrade Learning

Status: **rascunho**  
Data: **2026-08-10**

## 1. Visão

O NexusTrade será um robô único, permanentemente observando o mercado e capaz de
evoluir por meio de um laboratório de aprendizado de máquina. O robô visível ao
usuário executa sempre uma versão congelada, auditável e reversível chamada
**Champion**. O componente experimental, chamado **Challenger**, aprende, testa e
propõe mudanças sem poder aplicá-las sozinho.

O objetivo não é garantir lucro. O objetivo é construir um processo causal,
mensurável e governado para testar se filtros e configurações melhoram a estratégia
focal de Bollinger Bands.

## 2. Invariantes do produto

- Deve existir exatamente um registro NexusTrade por instalação.
- O registro é provisionado pelo sistema e não pode ser duplicado ou excluído pela
  criação comum de robôs.
- `nexus_trade` não aparece no catálogo de estratégias criáveis.
- Bollinger Bands permanece presente em toda versão de Champion e Challenger.
- O único ativo permitido é `R_100`; o Challenger não pode testar ou propor outro
  símbolo.
- Timeframe operacional é sempre M1.
- Expiração operacional é sempre 58 segundos.
- Nenhum Challenger pode se promover ou modificar um Champion.
- Nenhuma revisão de modelo pode alterar gerenciamento de risco ou permissões REAL.

## 3. Modos operacionais

### 3.1 OFF — aprendendo em DEMO

- O serviço, a coleta, a análise e o aprendizado permanecem ativos.
- O Champion congelado envia ordens DEMO de USD 0,35 como benchmark operacional.
- Um único Trial Challenger interno, congelado durante sua campanha de evolução, envia
  suas próprias ordens DEMO de USD 0,35.
- As duas trilhas são identificadas separadamente como `champion_baseline` e
  `challenger_trial`; trades, oportunidades, propostas e resultados nunca são
  misturados.
- As duas trilhas usam a mesma conta DEMO e podem possuir contratos distintos no mesmo
  período.
- Cada intent e trade persiste `lane`, versão da política, ciclo de avaliação e
  identificador do experimento.
- Cada lane admite no máximo um contrato ativo. Champion e Trial Challenger podem ter
  um contrato cada simultaneamente, sem permitir duplicidade dentro da mesma lane.
- Outros Challengers permanecem em replay, avaliação contrafactual ou modo sombra.
- Somente a conta DEMO reservada ao aprendizado pode receber ordens.
- Stake fixa de USD 0,35.
- Martingale, Soros e demais aumentos de stake ficam desabilitados.
- Continuam obrigatórias as proteções de execução: idempotência, ownership,
  prevenção de ordem duplicada, quarentena de resultado desconhecido e limite de
  concorrência.
- Se USD 0,35 não for aceito na proposta do contrato, a entrada é ignorada e o erro é
  registrado; o valor não é aumentado automaticamente.
- A comparação de 24 horas usa a mesma janela temporal e explicita qualquer diferença
  de oportunidades, horário de entrada, proposta, payout ou indisponibilidade entre
  as duas trilhas.
- Um único despachante é responsável pela conta DEMO compartilhada. Intents simultâneos
  são correlacionados e enviados de modo seguro sem misturar ownership ou settlement.
- O caminho de execução é acionado pela virada temporal do M1, sem aguardar o primeiro
  tick novo, com meta de despacho em até 250 ms, compra aceita no primeiro segundo e
  limite absoluto de 2 segundos; treinamento e relatórios nunca bloqueiam esse caminho.
- Uma recarga/reset da conta DEMO nunca altera P&L histórico nem apaga os resultados do
  ciclo; saldo e reset são eventos operacionais separados das métricas da estratégia.

### 3.2 ON — conta DEMO selecionada

- O Champion aprovado é a política operacional.
- A ordem usa a conta DEMO selecionada no aplicativo.
- Stake e gerenciamento seguem a configuração aprovada pelo usuário.
- A operação e seu resultado também alimentam o dataset.
- O Trial Challenger continua sua trilha interna de ordens DEMO de USD 0,35 sem
  gerenciamento financeiro; os outros candidatos continuam em replay/sombra.

### 3.3 ON — conta REAL selecionada

- O Champion aprovado é a política operacional.
- A ordem usa a conta REAL selecionada.
- Permanecem todas as confirmações, tickets e limites de segurança REAL do projeto.
- Uma revisão do Champion invalida qualquer autorização REAL anterior.
- O Trial Challenger continua sua trilha interna de ordens DEMO de USD 0,35 sem tocar
  a conta REAL; os outros candidatos continuam em replay/sombra.

### 3.4 Visibilidade e controle do Challenger

- OFF/ON, conta, stake e gerenciamento no aplicativo controlam somente o Champion.
- O usuário não recebe tela para iniciar, parar ou configurar diretamente o Challenger.
- O Challenger permanece um subsistema interno sempre ativo.
- A única interação exposta é a solicitação de atualização, com diff e métricas, para
  `Aprovar` ou `Enviar para reanálise`.

### 3.5 Parada total

Deve existir uma ação administrativa distinta de emergência que impeça qualquer
compra, inclusive das trilhas internas DEMO, sem impedir necessariamente a coleta
pública de mercado. Essa ação não faz parte do controle OFF/ON comum.

## 4. Transições de conta e versão

- Uma transição bloqueia novas entradas antes de trocar conta ou Champion.
- Contratos abertos continuam monitorados até liquidação.
- A troca ocorre somente quando não houver compra, intent ambíguo ou contrato aberto.
- Se o NexusTrade estiver ON, uma promoção não é aplicada.
- No modo OFF, o executor DEMO é pausado internamente, a versão é trocada de forma
  atômica e a análise é retomada.
- A API e a interface recebem a nova revisão por evento em tempo real, sem F5.

## 5. Dados e persistência

O coletor deve:

- Inicializar com `ticks_history` quando disponível.
- Assinar ticks em tempo real e persistir o fluxo continuamente.
- Produzir candles M1 causais e imutáveis depois de fechados.
- Detectar duplicatas, lacunas, reconexões e dados atrasados.
- Manter separados dados brutos, candles, características, previsões, oportunidades,
  ordens, resultados, datasets e artefatos de modelo.
- Registrar também oportunidades bloqueadas ou recusadas, permitindo avaliar a
  decisão `NÃO OPERAR`.
- O ADX não interrompe análise ou rotulagem; ele autoriza ou bloqueia somente a compra.
  Uma entrada bloqueada é consumida e exige novo sinal completo para outra tentativa.
- Versionar esquema de características e origem de cada dado.

Para alto volume, o desenho preferencial é Parquet particionado por símbolo/data com
consulta analítica por DuckDB. O SQLite operacional continua responsável pelo plano
de controle, trades, risco, ownership e metadados de versões.

Referências Deriv:

- [Ticks History](https://developers.deriv.com/docs/data/ticks-history/)
- [Ticks Stream](https://developers.deriv.com/docs/data/ticks/)
- [Price Proposal](https://developers.deriv.com/docs/trading/proposal/)

## 6. Solicitação de atualização

Quando um Challenger superar os gates definidos, o aplicativo apresenta:

- Champion atual e Challenger candidato.
- Janela exata de dados.
- Separação entre treino, validação e avaliação fora da amostra.
- Oportunidades, operações, recusas, wins, losses e empates.
- Assertividade, P&L, ROI, payout médio, payoff, expectancy e profit factor.
- Drawdown, exposição e sequências de wins/losses.
- Comparação dos dois candidatos no mesmo conjunto de avaliação.
- Comparação normalizada por stake, quantidade de operações e exposição; lucro bruto
  isolado não decide promoção.
- Quando múltiplas regras confirmarem uma única entrada, o relatório preserva todos os
  motivos sem contar a operação mais de uma vez.
- Indicadores adicionados, removidos e reconfigurados.
- Configuração anterior e proposta.
- Alterações nas condições de entrada e de `NÃO OPERAR`.
- Alertas de baixa amostra, drift, instabilidade ou dados incompletos.
- Identificador, hash e data do artefato candidato.
- Quebra de cada dia da campanha e consolidado semanal, sempre mostrando operações,
  wins, losses, empates e assertividade de Champion e Trial.
- Recomendação explicável `RECOMMEND_EVOLUTION`, `REANALYZE`, `INSUFFICIENT_SAMPLE` ou
  `DATA_INVALID`, incluindo ação sugerida e gates que sustentam a conclusão.
- Diff de indicadores, configurações, features e estratégia tanto nos relatórios de
  24 horas quanto no relatório semanal.

Uma solicitação de promoção só pode ser criada quando as duas condições mínimas forem
atendidas simultaneamente:

- ao menos uma semana completa do mesmo Trial Challenger;
- pelo menos 300 operações concluídas pelo Trial Challenger desde a última decisão
  humana que iniciou a campanha de evolução.

Antes disso, o relatório diário continua sendo produzido, mas recebe o estado
`SAMPLE_ACCUMULATING` e não oferece ação de promoção. Atingir os mínimos apenas libera
a avaliação dos demais gates; não prova superioridade nem garante recomendação.

O sistema usa governança conservadora para produzir a recomendação, mas jamais promove
automaticamente. Quando o candidato é elegível e recomendado, a decisão final entre
`Aprovar` e `Enviar para reanálise` é humana.

Depois de ao menos uma semana/300 operações, as duas ações permanecem humanas. Se a
recomendação for `REANALYZE`, uma aprovação excepcional exige confirmação reforçada e
exibe novamente todos os gates reprovados ou inconclusivos.

Percentuais apresentados como ganho representam melhoria histórica observada na janela
avaliada e nunca promessa de rentabilidade futura.

### 6.1 Continuidade entre semanas

- Cada semana de relatório começa segunda-feira às 10:00 e termina na segunda seguinte
  às 10:00 em `America/Sao_Paulo`.
- No fechamento, a semana vira snapshot imutável e uma nova semana visual começa em
  `dia 1/7` e `operações da semana 0`.
- Esse reset nunca apaga ticks, trades, features, labels, datasets, modelos, candidatos,
  métricas cumulativas, decisões ou relatórios anteriores.
- O progresso de evolução `operações acumuladas/300` não zera na virada semanal.
- Se uma semana terminar abaixo de 300 operações acumuladas, não existe sugestão de
  evolução; o acumulador continua na semana seguinte.
- Se a operação 300 ocorrer durante uma semana, todas as operações posteriores também
  entram no relatório e a decisão aguarda a próxima segunda-feira às 10:00.
- Falhar nos gates ou receber `Enviar para reanálise` não pausa nem reinicia o
  laboratório.
- Na virada semanal, o laboratório pode manter o Trial e carregar seu acumulador ou
  substituí-lo automaticamente pelo melhor candidato em sombra.
- Uma troca automática fecha a campanha anterior como `SUPERSEDED`, preserva seus
  dados e inicia o novo Trial com outro ID/hash e `operações acumuladas 0/300`.
- A troca nunca ocorre no meio da semana e nunca altera o Champion.
- Se existir proposta de evolução aguardando decisão humana, a troca automática fica
  suspensa até `Aprovar` ou `Enviar para reanálise`.
- `Aprovar` ou `Enviar para reanálise` encerra a campanha de evolução, cria seu snapshot
  imutável e zera somente o acumulador da próxima campanha.
- Se o Trial mudar após a decisão, a nova campanha registra outro ID/hash e nunca
  mistura suas métricas com a anterior.
- A campanha anterior permanece acessível no histórico do painel.

Ações disponíveis:

- `Aprovar`: promove somente após as travas operacionais.
- `Enviar para reanálise`: mantém o Champion, preserva o candidato e o devolve ao
  laboratório para novo ciclo.

O ciclo fecha diariamente às 10:00 no fuso IANA `America/Sao_Paulo`. O armazenamento
interno permanece em UTC, mas API, relatório e interface convertem e identificam o
horário de Brasília. Reiniciar a VPS não desloca a fronteira do ciclo.

## 7. Atualização da interface

- A tabela mostra um único NexusTrade fixo.
- O usuário não escolhe estratégia para esse registro.
- O usuário não escolhe ativo para esse registro; `R_100` é exibido como fixo.
- A tela diferencia claramente `OFF — aprendendo em DEMO`, `ON — DEMO`, `ON — REAL`,
  `aguardando liquidação`, `promoção pendente` e `parada total`.
- Mudanças de estado, métricas, proposta e versão chegam pelo WebSocket do bot.
- Toda transição interna relevante do laboratório possui reflexo obrigatório no
  frontend, inclusive Trial mantido/substituído, reset/continuidade de `operações/300`,
  recomendação, reanálise, bloqueio, erro, promoção e rollback; nenhuma delas pode ser
  silenciosa.
- A versão e a configuração completa do Champion permanecem sempre visíveis.
- Deve existir rollback explícito para uma versão anterior, sujeito às mesmas travas
  de parada e ausência de contrato aberto.
- Deve existir uma página dedicada `Evolução do NexusTrade`, detalhada em
  [FRONTEND.md](FRONTEND.md).
- A página é uma view interna do painel atual e preserva integralmente seu shell, KV,
  tokens visuais, tipografia, componentes e responsividade.
- A página possui dois níveis: relatórios diários/semanais e análise da campanha de
  evolução acumulada desde a última decisão humana.
- O histórico é navegável por calendário semanal; cada semana começa segunda-feira às
  10:00 de Brasília e filtros por dia isolado não substituem o recorte semanal.
- Relatórios diários, semanais e de evolução são exportáveis em CSV e Excel (`.xlsx`)
  a partir do mesmo snapshot persistido usado na interface.

## 8. Critérios de sucesso

- Robôs comuns não carregam dependências nem estado de ML.
- Nenhum evento NexusTrade é entregue a outro `bot_id`.
- Reinício da VPS não perde ticks persistidos, oportunidades, versão Champion,
  candidatos ou solicitações.
- Nenhuma promoção ocorre sem ação auditável do usuário.
- O mesmo dataset e artefato reproduzem o relatório apresentado.
- A operação REAL falha de forma fechada diante de modelo ausente, incompatível,
  corrompido ou não aprovado.

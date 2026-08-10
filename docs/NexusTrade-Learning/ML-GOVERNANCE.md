# Governança de aprendizado — Champion e Challenger

Status: **rascunho**

## 1. Separação de responsabilidades

### Champion

- É a única versão aprovada para operar quando o usuário coloca o NexusTrade em ON.
- É imutável durante a execução.
- Possui versão, configuração, esquema de características, artefato e hash.
- Pode ser V1, V2, V3 etc.; uma promoção cria nova versão em vez de sobrescrever.
- Sempre contém Bollinger como indicador primário.
- No OFF, envia operações DEMO de USD 0,35 em uma trilha própria para fornecer o
  benchmark operacional da comparação.

### Challenger

- É o único componente que treina e evolui e permanece ativo 100% do tempo.
- Um único candidato recebe o papel de Trial Challenger e envia ordens DEMO de
  USD 0,35 em uma trilha separada da trilha do Champion, independentemente de o
  Champion estar OFF ou ON.
- Trial Challenger e Champion OFF compartilham a mesma conta DEMO, com contratos,
  intents, versões e resultados separados por `lane`.
- O Trial Challenger é congelado durante toda campanha de evolução; configuração,
  features, regras, modelo e hash não mudam até a decisão humana. A campanha dura no
  mínimo uma semana e pode atravessar várias semanas até acumular 300 operações.
- Mudanças produzidas pelo laboratório durante a campanha criam outros candidatos em
  sombra e só podem disputar a próxima campanha.
- Os demais candidatos executam replay, avaliação contrafactual e modo sombra.
- Nunca envia ordem para a conta REAL nem usa o gerenciamento financeiro do Champion.
- Não pode alterar o ativo fixo `R_100`, o timeframe M1 ou a expiração de 58 segundos.
- Não possui controles diretos no aplicativo; somente sua proposta de promoção é
  apresentada ao usuário.
- Pode executar replay, avaliação contrafactual e modo sombra.
- Usa apenas o repertório autorizado.
- Não altera conta, stake, risco, timeframe, expiração ou permissões.
- Não pode promover a si próprio.

## 2. Repertório autorizado

### Bollinger e características próprias

- Período e desvios candidatos.
- `%B`, `z_score`, `width`.
- Inclinação e aceleração de `sma`, `upper_band` e `lower_band`.
- Distância do OHLC para cada banda.

Referência: [Bollinger Bands](https://python.stockindicators.dev/indicators/BollingerBands/).

### Regime, força e volatilidade

- ADX/DMI, inicialmente ADX(14).
- Choppiness Index, inicialmente CHOP(14).
- ATR e ATRP, inicialmente ATR(14).
- Aroon como candidato secundário.
- Keltner Channels, inicialmente Keltner(20, 2, 10), incluindo largura e relações de
  compressão/expansão com Bollinger.

Referências:

- [ADX](https://python.stockindicators.dev/indicators/Adx/)
- [Choppiness Index](https://python.stockindicators.dev/indicators/Chop/)
- [ATR/ATRP](https://python.stockindicators.dev/indicators/Atr/)
- [Aroon](https://python.stockindicators.dev/indicators/Aroon/)
- [Keltner Channels](https://python.stockindicators.dev/indicators/Keltner/)

### Osciladores e momentum

- RSI, inicialmente RSI(14).
- Estocástico, inicialmente Stoch(14, 3, 3, SMA).
- CCI, inicialmente CCI(20).
- ROC em períodos curtos a definir.

Referências:

- [RSI](https://python.stockindicators.dev/indicators/Rsi/)
- [Stochastic](https://python.stockindicators.dev/indicators/Stoch/)
- [CCI](https://python.stockindicators.dev/indicators/Cci/)
- [ROC](https://python.stockindicators.dev/indicators/Roc/)

### Médias móveis auxiliares

- SMA, EMA, WMA, HMA e KAMA em períodos candidatos controlados.
- Inclinação, aceleração e distância normalizada do preço.
- Cruzamentos como características, não como regras automáticas obrigatórias.
- Nenhuma média auxiliar substitui a linha central da Bollinger.

### Características do candle e contexto

- Corpo e pavios normalizados pelo range/ATR.
- Posição do fechamento nas bandas.
- Quantidade de candles fora da banda.
- Tempo desde o rompimento.
- Força e tamanho do candle de confirmação.
- Largura atual versus distribuição recente.
- Distância da SMA normalizada por ATR.
- Sequências recentes, horário e regime.

Indicadores baseados em volume ficam fora do repertório inicial até existir evidência
de que o feed usado oferece volume semanticamente adequado.

## 3. Ciclo de 24 horas

- O fechamento ocorre diariamente às 10:00 em `America/Sao_Paulo`.
- Cada ciclo cobre a janela contígua encerrada naquele instante e possui ID próprio.
- Timestamps persistidos em UTC são convertidos para Brasília na apresentação.
- Reinício, indisponibilidade ou atraso não muda a fronteira; um ciclo atrasado mantém
  sua janela original e registra a causa do atraso.
- O relatório é gerado todos os dias, mesmo sem candidato elegível.
- Promoção exige conjuntamente pelo menos uma semana completa do mesmo Trial e 300
  operações concluídas desde a última decisão humana.

1. Fechar a janela diária de dados.
2. Preservar a identidade/configuração do Trial Challenger durante a campanha.
3. Validar completude, ordem temporal e ausência de vazamento futuro.
4. Atualizar labels de oportunidades já encerradas.
5. Treinar/reconfigurar Challengers.
6. Avaliar em dados temporalmente posteriores não usados no treino.
7. Comparar Champion e Challenger na mesma amostra.
8. Se a amostra mínima não estiver completa, registrar `SAMPLE_ACCUMULATING`.
9. Se nenhum candidato passar os demais gates, registrar `CHAMPION_MANTIDO`.
10. Se houver candidato elegível e superior, criar solicitação `PENDING_USER_REVIEW`.

O ciclo diário é uma cadência de avaliação, não evidência suficiente por si só. Um
candidato pode precisar acumular vários ciclos antes de ser recomendável.

Os mínimos de 300 operações e uma semana completa são condições necessárias, mas não
suficientes.
O candidato ainda precisa superar os critérios de retorno, risco, estabilidade,
reprodutibilidade e complexidade.

### 3.1 Dois níveis de avaliação

#### Relatório diário — 24 horas

- Fecha às 10:00 de Brasília.
- Compara Champion e o mesmo Trial no dia concluído.
- Exibe dados, integridade, métricas, operações e diferenças observadas.
- Exibe sempre operações, wins, losses, empates, assertividade e diff completo de
  indicadores/configurações entre Champion e Trial.
- Não promove e não reinicia a campanha.

#### Evolução — campanha de uma ou mais semanas

- Agrega semanas e relatórios diários consecutivos do mesmo Trial.
- Exige ao menos uma semana completa e 300 operações concluídas acumuladas.
- Ultrapassar 300 não encerra coleta: todas as operações até a próxima segunda-feira às
  10:00 integram a avaliação.
- Executa gates conservadores de retorno, expectancy, drawdown, estabilidade,
  reprodutibilidade e complexidade.
- Produz recomendação explicável; a decisão final permanece humana.
- Mostra cada dia da semana e o consolidado correto da campanha, sem obter taxas por
  média simples de percentuais diários.
- Toda estimativa percentual é rotulada como resultado histórico da campanha, não como
  garantia de desempenho futuro.

### 3.2 Linha de aprendizado contínua

- O laboratório é permanente e cumulativo; não possui reset semanal de aprendizado.
- Coleta, rotulagem, treino e experimentos continuam durante e depois de cada campanha.
- A virada semanal fecha apenas uma janela de relatório e abre a próxima; não encerra
  automaticamente a campanha de evolução.
- Dados, artefatos, candidatos, hiperparâmetros testados e evidências negativas são
  preservados para evitar repetir experimentos ruins.
- `REANALYZE` mantém o Champion e devolve a evidência ao laboratório sem voltar ao zero.
- Falha nos gates também não reinicia o aprendizado.
- O Trial permanece imutável dentro da campanha. A campanha e seu acumulador só são
  encerrados por `Aprovar`, `Enviar para reanálise` ou substituição automática na
  fronteira semanal; qualquer troca cria novo ID/hash e meta `0/300`.
- A seleção automática do próximo Trial ocorre somente segunda-feira às 10:00 de
  Brasília e usa candidatos em sombra que passaram nos gates internos.
- Se nenhum candidato em sombra for adequado, o Trial atual e seu acumulador continuam.
- Proposta pendente de decisão humana congela a seleção automática até a decisão.
- Uma campanha substituída recebe estado `SUPERSEDED` e nunca mistura operações com a
  campanha sucessora.
- Toda manutenção ou substituição do Trial gera evento e snapshot próprios para o
  frontend, incluindo motivo, diff, versões, hashes e contadores.
- A mesma regra de visibilidade vale para toda transição relevante do laboratório:
  continuidade/reset de campanha, resultado dos gates, recomendação, reanálise, erro,
  promoção e rollback. Estado interno relevante sem evento e sem snapshot de interface
  é inválido.
- O histórico cumulativo participa do treino e das verificações de estabilidade, mas o
  painel semanal mantém métricas delimitadas por campanha para comparação legível.
- Relatórios e exports são derivados de snapshots imutáveis; exportar novamente o mesmo
  snapshot preserva números, versões e critérios originais.

A comparação deve considerar, na mesma janela temporal, quantidade de operações,
wins/losses, assertividade, P&L, ROI sobre stake, payout, payoff, expectancy, profit
factor, drawdown, exposição, sequências e estabilidade. Como as estratégias podem
operar quantidades diferentes, lucro bruto sozinho não define superioridade.

## 4. Promoção manual

- A proposta mostra diff completo de indicadores, configurações, features, regras e
  métricas.
- `Aprovar` exige Champion OFF e ausência de contrato/intents pendentes na trilha do
  Champion; o laboratório Challenger não é desligado pela interface comum.
- O artefato é validado, persistido e ativado atomicamente.
- O Champion anterior permanece disponível para rollback.
- `Enviar para reanálise` nunca altera o Champion.
- Toda ação registra usuário, horário, versão de origem, versão alvo e hash.
- A interface recebe o resultado por WebSocket, sem recarregar a página.

## 5. Proteções contra degradação

- Proibida promoção automática.
- Proibido treinar e avaliar no mesmo intervalo temporal.
- Proibido selecionar candidato apenas por assertividade.
- Exigir amostra mínima, P&L/expectancy, drawdown, estabilidade entre janelas e
  comparação com baseline.
- Penalizar complexidade e excesso de indicadores.
- Detectar drift; diante de incerteza, recomendar `NÃO OPERAR`, nunca trocar modelo.
- Validar compatibilidade entre modelo, código e esquema de características.
- Rollback é uma nova ação auditada, não edição destrutiva de histórico.

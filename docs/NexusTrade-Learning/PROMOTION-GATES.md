# Gates de promoção — recomendação conservadora

Status: **configuração inicial aprovada para implementação**  
Data: **2026-08-10**

## 1. Princípio

Os mínimos de uma semana completa e 300 contratos liquidados apenas habilitam a
avaliação. Eles não provam que o Trial é superior. Uma promoção deve combinar:

1. dados e execução íntegros;
2. ganho econômico material;
3. evidência estatística fora da amostra;
4. risco não degradado;
5. estabilidade entre dias e regimes;
6. complexidade controlada e explicável.

O indicador principal de promoção será a **expectancy líquida normalizada por stake**:

```text
expectancy_normalizada = soma(profit_final_realizado) / soma(buy_price)
```

O `profit_final_realizado`, `buy_price` e `payout` são persistidos a partir do contrato
liquidado da Deriv. Assertividade, isoladamente, é somente diagnóstico: um robô pode
acertar mais e ainda ganhar menos se aceitar payouts piores.

Como cada contrato do laboratório usa stake fixa de USD 0,35, todo relatório apresenta
obrigatoriamente as contagens brutas `N`, `wins`, `losses` e `empates`, além da
assertividade:

```text
N_decisivo = wins + losses
assertividade = wins / N_decisivo * 100
N_total = wins + losses + empates
capital_arriscado = N_total * USD 0,35
```

Empates são mostrados separadamente e não entram no denominador da assertividade, pois
não representam acerto nem erro. A interface também mostra `wins/N_decisivo`, por
exemplo `174/300 (58,00%)`, para que a porcentagem nunca apareça sem a amostra que a
originou. Se a Deriv liquidar um contrato classificado como empate com lucro/prejuízo
diferente de zero, o resultado financeiro real continua entrando normalmente na
expectancy e o caso recebe destaque de reconciliação.

Assertividade não é um gate único de promoção, mas participa da explicação, das análises
diária, semanal, acumulada, por regra e por regime. Também é comparada com a taxa de
acerto de equilíbrio calculada a partir dos payouts realmente recebidos.

Champion e Trial são comparados na mesma janela de calendário, com contratos reais DEMO
e stake de USD 0,35. Quantidades diferentes de operação são normalizadas por capital
arriscado, exposição e blocos temporais.

## 2. Gates não negociáveis

Falha em qualquer item desta seção impede a criação de uma recomendação de promoção e
não pode ser ignorada por aprovação excepcional:

- mesmo Trial, ID, hash, regras, features e modelo congelados durante toda campanha;
- ao menos uma semana completa e 300 contratos Trial liquidados;
- ativo `R_100`, timeframe M1, expiração de 58 segundos e Bollinger obrigatória;
- zero vazamento de dados futuros entre treino e avaliação;
- 100% dos contratos usados no relatório reconciliados com o resultado final da Deriv;
- nenhum contrato duplicado, sem lane ou com resultado financeiro ambíguo;
- nenhum sinal executado depois do limite de 2 segundos;
- cobertura de candles válidos de pelo menos 99,5% na janela; lacunas são identificadas,
  excluídas das comparações afetadas e explicitadas no relatório;
- artefato reproduzível a partir do snapshot, versão de código, schema e configuração;
- Champion OFF e sem intent/contrato pendente no momento da eventual promoção.

Resultado de falha: `DATA_INVALID`, `INSUFFICIENT_SAMPLE` ou bloqueio operacional,
conforme a causa.

## 3. Gate primário de desempenho

Todos os itens devem passar:

- expectancy normalizada do Trial maior que zero;
- profit factor do Trial `>= 1,10`;
- melhoria observada do Trial sobre o Champion de pelo menos **2 pontos percentuais**
  na expectancy normalizada;
- limite inferior unilateral de 95% para a diferença
  `expectancy_trial - expectancy_champion` maior que zero;
- probabilidade reamostrada de o Trial superar o Champion `>= 95%`.

A inferência usa bootstrap em blocos temporais, e não sorteio ingênuo de trades
independentes, para preservar sequências, horários e regimes. Se 300 operações não
forem suficientes para fechar o intervalo com confiança, a campanha continua
acumulando evidência.

Os 2 pontos percentuais representam materialidade econômica, não promessa de retorno.
Esse limiar poderá ser recalibrado somente após existir histórico real suficiente e
por decisão versionada.

## 4. Gates de risco

Todos devem passar:

- drawdown máximo do Trial normalizado por stake no máximo 5% pior que o Champion;
- recovery factor do Trial igual ou superior ao do Champion;
- pior resultado em bloco móvel de 50 operações no máximo 10% pior que o Champion;
- maior sequência de losses no máximo duas operações acima da sequência do Champion;
- zero quebra de limite de simultaneidade, stake, conta, ativo ou expiração.

Uma melhora de retorno não compensa degradação ilimitada de risco. Se o Trial produzir
retorno materialmente maior com risco levemente acima desses limites, a máquina retorna
`REANALYZE`; depois dos gates não negociáveis, o usuário ainda pode fazer uma aprovação
excepcional com confirmação reforçada.

## 5. Gates de estabilidade

Todos devem passar:

- Trial supera o Champion na expectancy diária em pelo menos 60% dos dias completos da
  campanha;
- expectancy do Trial é positiva em pelo menos 60% dos dias completos;
- nenhum dia isolado responde por mais de 40% do lucro líquido positivo da campanha;
- não há deterioração clara nas três janelas diárias mais recentes;
- em cada regime com amostra suficiente — mínimo de 30 operações — não existe perda
  estatisticamente relevante escondida pelo consolidado.

Os regimes iniciais são faixas de ADX, largura da Bollinger/ATRP, horário de Brasília e
tipo de entrada: banda superior, banda inferior ou linha central. Regime sem 30
operações aparece como `inconclusivo`, não como aprovado.

## 6. Proteção contra overfitting e seleção do “melhor por sorte”

- Todo candidato, hiperparâmetro e indicador testado entra em um ledger imutável de
  tentativas, inclusive resultados ruins.
- A evidência de promoção vem somente da campanha forward DEMO do Trial congelado;
  backtest, replay e shadow servem para selecionar Trial, nunca para promover Champion.
- A probabilidade de desempenho positivo ajustada pela quantidade de tentativas,
  usando Deflated Sharpe Ratio ou teste equivalente, deve ser `>= 95%`.
- A Probability of Backtest Overfitting do processo de seleção deve ser `<= 10%` quando
  houver blocos suficientes para calculá-la; resultado inconclusivo não passa.
- O candidato deve continuar superior em análise de sensibilidade, sem depender de um
  único valor exato de parâmetro.

## 7. Orçamento de mudança

Para preservar a identidade do NexusTrade e permitir atribuição causal, uma promoção
altera no máximo **uma família material por campanha**:

- reconfiguração de indicadores existentes; ou
- adição/remoção de um indicador auxiliar; ou
- alteração de um filtro/regra de entrada ou `NÃO OPERAR`; ou
- troca do modelo de decisão.

Bollinger nunca pode ser removida. Quando houver indicador novo, uma ablação deve
demonstrar que retirar esse indicador reduz a expectancy ou piora o risco fora da
amostra. Candidato com várias mudanças simultâneas deve ser dividido em campanhas
sequenciais antes de poder chegar ao Champion.

## 8. Estados e decisão humana

- `RECOMMEND_EVOLUTION`: todos os gates passaram.
- `REANALYZE`: amostra mínima passou, mas desempenho, risco, estabilidade, seleção ou
  complexidade falhou.
- `INSUFFICIENT_SAMPLE`: ainda não há amostra mínima ou a evidência estatística continua
  inconclusiva; a campanha pode continuar acumulando.
- `DATA_INVALID`: integridade, reconciliação ou reprodutibilidade falhou.

A recomendação é conservadora e a decisão final é humana. Aprovação excepcional somente
é possível depois da amostra mínima e nunca ignora gates não negociáveis. O frontend
mostra valor, limiar, estado, evidência, motivo e impacto de cada gate.

## 9. Conteúdo obrigatório no frontend

O painel mostra, para Champion e Trial e para cada dia/campanha:

- `N_total`, `N_decisivo`, wins, losses, empates, `wins/N_decisivo` e assertividade;
- capital arriscado, sempre reconciliado com a stake fixa de USD 0,35 no laboratório;
- taxa de acerto de equilíbrio e margem da assertividade acima/abaixo desse equilíbrio;
- payout médio e distribuição de payouts;
- profit final, ROI sobre stake, expectancy e profit factor;
- drawdown, recovery factor, pior bloco e sequência máxima de losses;
- diferença Trial menos Champion, intervalo de confiança e probabilidade de
  superioridade;
- estabilidade diária e por regime;
- número total de candidatos/configurações testados;
- DSR/PBO ou estado `inconclusivo`;
- diff de indicadores, parâmetros, features, regras e modelo;
- cada gate com `PASSOU`, `FALHOU` ou `INCONCLUSIVO`;
- recomendação em linguagem clara e ressalva de que resultado histórico não garante
  desempenho futuro.

Toda atualização chega sem F5 e permanece disponível no snapshot histórico e nos
exports CSV/XLSX.

## 10. Fundamentos da recomendação

- [The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551):
  corrige inflação de desempenho causada por múltiplas tentativas e retornos não normais.
- [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253):
  propõe validação combinatória para estimar o risco de seleção overfit.
- [Backtesting Strategies Based on Multiple Signals](https://www.nber.org/papers/w21329):
  mostra como combinar muitos sinais aumenta severamente o viés de overfitting.
- [Harvey, Liu e Zhu — múltiplos testes](https://www.nber.org/papers/w20592):
  demonstra que o limiar estatístico tradicional é insuficiente após muitas tentativas.
- [Deriv Open Contract Status](https://developers.deriv.com/docs/trading/proposal-open-contract/):
  fonte operacional para acompanhar o contrato e reconciliar seu resultado final.

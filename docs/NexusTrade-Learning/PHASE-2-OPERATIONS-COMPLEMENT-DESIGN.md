# NexusTrade — complemento operacional da Fase 2

Status: **desenho aprovado em 2026-08-11**  
Escopo: observabilidade ao vivo e gerenciamento exclusivo do Champion

## 1. Fontes de verdade

Este complemento obedece integralmente a:

- [STRATEGY.md](STRATEGY.md), para regras causais, Bollinger, ADX, R_100, M1,
  expiração de 58 segundos, entrada na abertura e uma posição por lane;
- [PRD.md](PRD.md), para isolamento Champion/Trial, modos OFF/ON e segurança REAL;
- [FRONTEND.md](FRONTEND.md), para transparência, KV atual, atualização sem F5 e
  governança humana;
- [ML-GOVERNANCE.md](ML-GOVERNANCE.md), para o limite do gate ML e separação entre
  execução versionada e laboratório.

Nenhum item deste documento altera a estratégia. Ele torna visível o que já é
executado e libera somente a configuração de dinheiro/risco do Champion ON.

## 2. Decisão de arquitetura

O NexusTrade mantém seu workspace dedicado. A aba `OPERAÇÃO` recebe um monitor ao
vivo próprio, porque o workspace dos robôs comuns representa apenas um executor e não
consegue separar Champion e Trial. O monitor reutiliza `TradingChart` e o KV atual,
mas possui estado e componentes NexusTrade próprios.

Alternativas rejeitadas:

1. Reexibir o workspace padrão: simples, mas mistura as duas lanes e esconde decisões
   `NÃO OPERAR`.
2. Criar outra página: separa a navegação, perde contexto e duplica shell/autenticação.

## 3. Monitor operacional

### 3.1 Gráfico

- Uma instância de `TradingChart` renderiza R_100 em candles M1.
- A vela viva é marcada como parcial e nunca aparece como confirmação de setup.
- O gráfico mostra Bollinger superior, SMA central e Bollinger inferior da versão
  Champion vigente.
- Marcadores identificam rompimento externo, confirmação contrária, cruzamento
  central, CALL, PUT, bloqueio por ADX, bloqueio stale e bloqueio pelo gate ML.
- Champion e Trial usam formas/rótulos diferentes; cor nunca é a única distinção.
- ADX é exibido como valor textual e estado `AUTORIZADO (<= 22)` ou `BLOQUEADO (> 22)`.
- A leitura essencial continua disponível em tabela/journal, sem depender de hover.

### 3.2 Lanes e contratos

Cada lane possui card independente:

- versão e papel (`Champion` ou `Trial`);
- conta mascarada apenas como `DEMO` ou `REAL`, sem identificador no monitor;
- estado da máquina e motivo do último bloqueio;
- contrato atual: direção, stake, preço de entrada, spot atual, horário de compra,
  expiração, contagem regressiva, status e P&L quando disponível;
- meta de entrada e latência observada;
- estado vazio explícito quando não há posição.

Champion e Trial podem ter um contrato cada simultaneamente. A UI nunca combina os
dois como se fossem uma única posição.

### 3.3 Journal

O journal oferece `TODAS`, `CHAMPION`, `TRIAL` e `DECISÕES BLOQUEADAS`.
Cada linha mostra horário de Brasília, lane, versão, regra causal, direção/ação,
ADX, resultado do gate ML, stake, latência, contrato, resultado e P&L. Decisões sem
ordem permanecem visíveis com `reason_codes` traduzidos, sem inventar resultado.

## 4. Contrato de dados ao vivo

O backend amplia os eventos NexusTrade sem alterar os oito eventos governados
existentes:

- `nexus.market_history`: janela limitada de candles M1 e linhas de Bollinger;
- `nexus.market_tick`: tick atual, candle parcial e valores de linha disponíveis;
- `nexus.position`: abertura/atualização de contrato por lane;
- `nexus.decision` e `nexus.trade`: continuam sendo a fonte persistida de decisões e
  liquidações.

Todos usam `bot_id=nexus-trade`, `event_id`, `schema_version=1`,
`snapshot_version>=1` e payload sanitizado. Reconexão busca snapshot durável antes de
reativar ações. O snapshot inclui uma janela limitada de mercado, estados de lane e
posições atuais, suficiente para reconstruir a tela sem eventos antigos.

O runtime publica observabilidade fora do caminho crítico da compra. Falha de
publicação não atrasa nem repete ordem. Nenhum tick, candle ou valor visual pode
alterar decisão já persistida.

## 5. Gerenciamento do Champion

Um botão `CONFIGURAR GERENCIAMENTO` aparece somente para o Champion. O formulário
edita:

- modelo: `fixed`, `martingale` ou `soros`;
- stake inicial;
- multiplicador e níveis máximos do Martingale;
- níveis e percentual do Soros;
- meta diária e stop diário;
- máximo de operações por dia;
- stake máxima;
- perdas consecutivas máximas;
- cooldown em minutos.

Regras:

- só pode salvar com Champion OFF e lane Champion `IDLE`;
- o Trial continua DEMO, US$ 0,35 e sem controles;
- no modo OFF, o Champion continua DEMO, US$ 0,35 e sem gerenciamento de risco;
- no modo ON, o Champion usa a configuração persistida e a conta selecionada;
- R_100, M1, 58 segundos, Bollinger, ADX e regras de entrada não são campos editáveis;
- o backend valida números finitos, limites positivos e consistência por modelo;
- em conta REAL, stake inicial e stake calculada nunca ultrapassam
  `REAL_MAX_STAKE_USD`; divergência bloqueia a ordem;
- a configuração recebe revisão monotônica, é persistida atomicamente e publicada
  sem F5;
- o runtime recarrega a configuração somente numa fronteira segura, sem contrato ou
  intent do Champion. Reinício reconstrói exatamente a revisão persistida.

## 6. Experiência e responsividade

No desktop, o gráfico ocupa a coluna principal e os dois cards de lane formam a
coluna operacional. O journal vem abaixo. Em tablet e mobile, a ordem é: status geral,
gráfico, Champion, Trial, filtros e journal. Controles primários têm pelo menos 44 px
em ponteiro grosso.

Estados `LIVE`, `ATRASADO`, `RECONECTANDO`, `PARCIAL` e `SEM DADOS` são textuais. O
último estado válido permanece visível durante reconnect, com horário da última
atualização. Movimento respeita `prefers-reduced-motion`.

## 7. Erros e segurança

- Configuração inválida recebe 422 sem mutação.
- Revisão vencida recebe 409, força reidratação e não aplica UI otimista.
- Champion ON, lane não-IDLE ou contrato aberto bloqueiam edição.
- Mercado atrasado não apaga dados; marca `ATUALIZAÇÃO ATRASADA`.
- Evento duplicado ou fora de ordem é ignorado pelo store.
- Nenhum token, ticket, conta completa, caminho local ou credencial entra no DOM,
  WebSocket, logs ou exports.
- O desenvolvimento e QA usam somente DEMO, `ALLOW_REAL_TRADING=false` e teto REAL
  zero. A validação REAL permanece suspensa até este complemento obter GO.

## 8. Testes e critérios de aceite

- RED/GREEN para validação e persistência do gerenciamento, revisão concorrente,
  fronteira segura e teto REAL.
- RED/GREEN para envelopes de mercado/posição, idempotência e reconstrução após
  reconnect.
- Testes JS para gráfico, markers, cards, filtros, journal e formulário.
- Regressão integral de Donchian e Nexus Speed sem diff comportamental.
- Docker local com DEMO real da Deriv, API e bot saudáveis, zero restart loop e zero
  traceback.
- Navegador real em 1440x900, 1024x768, 768x1024, 390x844 e 360x800.
- Verificação manual de candle parcial, decisão bloqueada, contrato por lane,
  liquidação, reconnect sem F5 e persistência do gerenciamento após restart.
- O complemento recebe GO somente quando o usuário consegue acompanhar a execução e
  revisar a configuração do Champion antes de iniciar.

## 9. Fora de escopo

- alterar estratégia ou indicadores;
- permitir configuração do Trial;
- adicionar outro ativo/timeframe/duração;
- promover modelos automaticamente;
- executar validação REAL durante a implementação;
- substituir Lightweight Charts ou introduzir framework frontend.

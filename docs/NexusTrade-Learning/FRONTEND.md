# Frontend — evolução do NexusTrade

## 0. Invariante de transparência

Todo estado interno relevante do NexusTrade deve possuir representação correspondente
no frontend. Isso inclui manutenção ou substituição do Trial, reset ou continuidade do
contador de evolução, fechamento de campanha, recomendação, bloqueio por gate,
promoção, reanálise, rollback, erro de dados e mudança de versão/configuração. O backend
nunca pode executar silenciosamente uma dessas transições: deve persistir o evento,
publicar a atualização em tempo real e disponibilizar seu snapshot no histórico.

Quando não houver mudança, o painel também deve informar explicitamente que o Trial foi
mantido e por quê. A ausência de toast não pode ser a única indicação de ausência de
alteração.

Status: **implementado e validado**  
Fuso exibido: **America/Sao_Paulo**

## 1. Objetivo

Criar uma página dedicada para o usuário entender, comparar e decidir sobre uma
evolução proposta pelo Trial Challenger. O relatório é gerado programaticamente a
partir de dados persistidos; números e textos técnicos não são preenchidos manualmente.

O Challenger permanece invisível como robô controlável. A página expõe somente seu
Trial, evidências, proposta e histórico de decisões.

## 2. Integração com o KV atual

- A experiência permanece dentro do shell atual: command bar, rail de robôs e área
  `.workspace`.
- Não existe tema, marca, login ou navegação paralela para o NexusTrade.
- Reutilizar os tokens existentes: `--bg`, `--panel`, `--panel-2`, `--line`, `--text`,
  `--muted`, `--cyan`, `--green`, `--red`, `--amber` e `--blue`.
- Reutilizar DM Sans para interface e JetBrains Mono para números, versões e métricas.
- Reutilizar padrões existentes: `.panel`, `.panel-head`, `.eyebrow`, `.status-chip`,
  `.primary-button`, `.secondary-button`, tabelas, drawer, confirmação e toast.
- Novos componentes devem respeitar os breakpoints e o comportamento responsivo do
  painel atual.
- A evolução pode ampliar o sistema visual existente, mas não trocar seu KV.

## 3. Entrada na página

- A linha fixa do NexusTrade mostra Champion atual, modo OFF/ON, conta operacional,
  estado do ciclo e existência de proposta pendente.
- Ao selecionar NexusTrade no rail, o painel mantém a visão operacional do Champion.
- Uma ação `Ver evolução` na área de ações do Champion troca a `.workspace` para a
  view de evolução sem recarregar o shell.
- A view possui ação clara `Voltar ao painel` para restaurar gráfico, operação atual,
  risco e journal do Champion.
- Uma nova proposta gera badge/notificação sem recarregar o aplicativo.
- O usuário nunca cria, inicia, pausa ou exclui um Challenger.

### 3.1 Dois níveis de navegação

A view oferece duas abas internas, preservando o shell do painel:

- `Relatórios`: ciclos diários e semanas fechadas.
- `Evolução do Champion`: campanha acumulada desde a última decisão humana.

Trocar de aba não recarrega a aplicação e mantém o relatório selecionado no estado do
cliente e, quando possível, em um deep-link do próprio painel.

## 4. Cabeçalho

Exibir:

- `Champion Vn` e status OFF/ON.
- `Trial Challenger <id>` e ciclo avaliado.
- Janela do relatório em horário de Brasília.
- Ativo fixo `R_100`, timeframe M1 e expiração de 58 segundos.
- Estado: `coletando`, `avaliando`, `sem melhoria`, `proposta pendente`, `reanálise`,
  `aprovando`, `aprovado`, `falhou` ou `amostra insuficiente`.
- Durante formação de amostra, mostrar separadamente `semana atual: dia n/7`,
  `operações desta semana` e `evolução: operações acumuladas/300`; a ação de aprovação
  não existe antes de uma semana completa e 300 operações acumuladas.

## 5. Comparativo Champion × Trial Challenger

Uma tabela usa as mesmas definições e a mesma janela para os dois lados:

| Grupo | Métricas |
|---|---|
| Amostra | oportunidades, executadas, bloqueadas por ADX, não operadas |
| Resultado | wins, losses, empates, assertividade |
| Financeiro | stake, payout médio, P&L, ROI, payoff, expectancy, profit factor |
| Risco | drawdown máximo, exposição, sequência máxima de wins/losses |
| Execução | latência p50/p95, entradas stale, erros/reconexões |
| Estabilidade | janelas positivas/negativas, drift, confiança e tamanho da amostra |

O frontend destaca diferença absoluta e percentual, mas nunca classifica candidato
apenas por lucro bruto ou assertividade.

### 5.1 Nível diário — 24 horas

- Seletor/lista dos ciclos fechados às 10:00.
- Métricas Champion × Trial daquele período.
- Exibir sempre operações concluídas, wins, losses, empates e assertividade de cada
  lane, com diferença absoluta e percentual.
- Lista auditável das operações e sinais bloqueados.
- Progresso da semana visual e da campanha de evolução acumulada.
- Diff completo Trial × Champion de indicadores, configurações, features e regras,
  mesmo quando não houve mudança desde o relatório anterior.
- Nenhuma ação de promoção.

### 5.2 Nível semanal e evolução acumulada

- Cada semana vai de segunda-feira às 10:00 até a segunda seguinte às 10:00 em
  `America/Sao_Paulo`.
- Um calendário permite selecionar somente semanas; dias isolados não são filtros do
  relatório histórico.
- O calendário destaca início, fim, estado e existência de proposta em cada semana.
- A semana selecionada mostra cada dia e a linha `SEMANA COMPLETA`.
- A evolução mostra o acumulado desde a última decisão humana, podendo incluir várias
  semanas consecutivas do mesmo Trial.
- Cada linha diária mostra data, janela, `N_total`, `N_decisivo`, wins, losses, empates,
  `wins/N_decisivo`, assertividade, taxa de equilíbrio pelo payout, P&L, ROI,
  expectancy e drawdown de Champion e Trial.
- No laboratório de USD 0,35, o painel mostra também o capital arriscado como
  `N_total × USD 0,35` e valida essa soma contra os contratos persistidos.
- A linha `SEMANA COMPLETA` soma contagens e P&L e recalcula corretamente percentuais e
  métricas agregadas; não calcula assertividade pela média simples dos dias.
- Gates conservadores com estado `passou`, `falhou` ou `inconclusivo` e justificativa.
- Diff completo de indicadores, configurações, features, modelo e estratégia.
- Recomendação final do sistema e decisão humana.
- Ao fechar a semana, o relatório vira snapshot histórico e a view semanal ativa inicia
  em `dia 1/7` e `operações da semana 0`.
- O acumulador de evolução não zera; se estiver abaixo de 300, continua somando na
  semana seguinte e nenhuma sugestão é apresentada.
- Se ultrapassar 300 durante a semana, continua contabilizando tudo até a próxima
  segunda-feira às 10:00.
- Se o laboratório trocar automaticamente Trial A por Trial B na fronteira semanal, a
  UI arquiva A como `SUPERSEDED`, explica o motivo e mostra B em `0/300`.
- Se o Trial for mantido, seu contador acumulado continua sem reset.
- A troca mostra `antes → depois` para versão, hash, indicadores, configurações,
  features, regras e modelo, mesmo sem exigir ação do usuário.
- Um badge e toast avisam a mudança; o painel atualiza sem F5.
- O reset visual não apaga nem reinicia qualquer dado ou processo do backend.
- O seletor de campanhas permite reabrir semanas anteriores exatamente como foram
  apresentadas.

### 5.3 Recomendação explicável

A máquina de decisão do aprendizado avalia a campanha acumulada no fechamento semanal
e retorna um estado:

- `EVOLVE`: candidato elegível e superior nos gates conservadores.
- `REANALYZE`: candidato não demonstrou superioridade estável ou falhou em gates.
- `INCONCLUSIVE`: amostra, integridade ou evidência ainda não permite uma recomendação.

A recomendação mostra:

- resumo em linguagem clara;
- gates aprovados, reprovados e inconclusivos;
- dias em que o Trial ganhou ou perdeu do Champion;
- contribuição de cada tipo de sinal e indicador;
- melhoria observada em assertividade, ROI, expectancy e drawdown;
- confiança, tamanho da amostra e riscos detectados;
- ação sugerida: `Evoluir` ou `Enviar para reanálise`.

Textos como `melhoria observada de X%` referem-se somente à campanha concluída. A UI
nunca apresenta esse número como garantia de ganho futuro.

### 5.4 Exportação

Todo relatório diário, semanal ou de evolução pode ser exportado a partir de seu
snapshot persistido:

- `CSV`: pacote ZIP com arquivos separados para resumo, dias, operações Champion,
  operações Trial, indicadores/configurações, gates e auditoria.
- `Excel (.xlsx)`: workbook com abas `Resumo`, `Dias`, `Champion`, `Trial`,
  `Indicadores`, `Gates` e `Auditoria`.

Os exports incluem fuso, janela, IDs, versões, hashes e data de geração. Os números são
gerados no backend a partir do mesmo snapshot da tela, não remontados do DOM. Exportar
não altera estado, relatório, campanha ou recomendação.

## 6. Diferenças da proposta

Exibir um diff estruturado, sem depender de texto livre:

- Bollinger: período, desvio e recursos utilizados.
- ADX: período e limite.
- Indicadores adicionados, removidos ou mantidos.
- Configuração exata de cada indicador.
- Features de candles adicionadas/removidas.
- Condições de entrada e `NÃO OPERAR` alteradas.
- Modelo/algoritmo, hiperparâmetros, versão do schema e hash do artefato.
- Invariantes preservados: Bollinger presente, `R_100`, M1 e 58 segundos.

Toda alteração deve mostrar `antes → depois` e uma justificativa apoiada pelas métricas.
O mesmo diff aparece no relatório diário, semanal e na evolução acumulada. Cada janela
mostra a configuração imutável do Trial aplicável àquele período.

## 7. Visualizações

- Curva de resultado acumulado Champion × Challenger na mesma escala.
- Drawdown ao longo da janela.
- Operações por hora e resultado.
- Distribuição de wins/losses por tipo de sinal: banda superior, inferior e central.
- Motivos de `NÃO OPERAR` e bloqueios por ADX.

Gráficos são derivados do payload do relatório e devem continuar legíveis em desktop e
mobile. Valores tabulares permanecem disponíveis para auditoria e acessibilidade.

## 8. Ações

### Aprovar

- Disponível somente com Champion OFF.
- Exige ausência de intent/contrato pendente na lane do Champion.
- Mostra confirmação com versão atual, versão candidata e resumo do diff.
- Pausa novas ordens da lane do Champion, aplica a versão atomicamente e publica o novo
  snapshot por WebSocket.
- A tela muda para `Champion Vn+1` sem F5.
- Se a recomendação for `REANALYZE`, a aprovação continua possível como exceção humana,
  mas exige confirmação reforçada com os gates reprovados/inconclusivos.

### Enviar para reanálise

- Mantém o Champion atual.
- Preserva candidato, relatório e decisão no histórico.
- Permite uma observação opcional do usuário.
- O laboratório considera o candidato em ciclos posteriores sem garantia de nova
  recomendação.
- A ação não zera campanha histórica, dataset, modelos ou progresso interno do
  laboratório; a próxima janela visual começa normalmente.

## 9. Histórico e rollback

- Listar propostas aprovadas, enviadas para reanálise e expiradas.
- Cada item mostra data/hora de Brasília, usuário, versões, resumo e hash.
- Abrir um item reproduz o relatório original, sem recalcular números atuais.
- Rollback é uma ação explícita e auditada, disponível apenas com Champion OFF e sem
  contrato pendente.

## 10. Atualização em tempo real

A página consome o WebSocket existente por `bot_id` para receber:

- progresso e fechamento do ciclo;
- nova proposta;
- Trial mantido ou substituído na fronteira semanal;
- mudança de decisão;
- promoção e nova versão do Champion;
- erros e recuperação.

Após reconexão, o cliente busca snapshot persistido para reparar eventos perdidos.

## 11. Estados de erro e segurança

- Dados incompletos bloqueiam aprovação.
- Métricas não reproduzíveis bloqueiam aprovação.
- Modelo incompatível/corrompido bloqueia aprovação.
- Se o Champion ficar ON durante a revisão, `Aprovar` é desabilitado imediatamente.
- Falha durante promoção mantém ou restaura o Champion anterior.
- Nenhum token, OTP ou dado secreto aparece no relatório ou WebSocket.
- Aprovar, reanalisar e fazer rollback pede a credencial humana em um campo `password`
  transitório. O cliente envia apenas `X-Nexus-Human-Key`, nunca persiste o valor e
  limpa campo e referência em memória ao concluir ou cancelar a ação.

## 12. Workspace operacional implementado

A aba `OPERAÇÃO` é a tela inicial do singleton NexusTrade e expõe, sem misturar
controles do Trial:

- gráfico R_100/M1 em tempo real com preço e Bollinger (20, 2, SMA);
- ADX (14), gate `ADX <= 22`, última decisão e estado da conexão;
- filtros `TODAS`, `CHAMPION` e `TRIAL`;
- uma posição ao vivo por lane, com direção, stake, entrada, spot, P&L e expiração;
- journal de decisões e contratos liquidados;
- cards separados do Champion versionado e do Trial invisível ao controle humano.

O botão `GERENCIAMENTO` é exclusivo do Champion. Ele só fica habilitado com o
Champion OFF, lane IDLE, snapshot atual e parada total desativada. O formulário oferece
Fixed, Martingale e Soros, além de meta, stop, máximo diário de operações, stake
máxima, losses consecutivos e cooldown. A estratégia, ativo, timeframe, duração,
Bollinger e ADX não são editáveis.

Ao clicar `INICIAR CHAMPION`, o mesmo formulário é aberto obrigatoriamente com a
configuração persistida. O backend salva por CAS e somente depois habilita o Champion.
Em OFF, Champion e Trial ignoram esse gerenciamento e continuam em DEMO a USD 0,35.
Em ON, todos os limites são reavaliados antes do transporte da ordem; um bloqueio é
gravado como `execution_blocked_reason` e nunca impede a coleta do Trial.

# NexusTrade Learning

Status: **desenho aprovado; pronto para execução em duas fases**  
Última atualização: **2026-08-10**

Esta pasta é a fonte de verdade para o desenho do NexusTrade, o único robô do
NexusTrader com aprendizado de máquina. Nenhuma regra descrita como pendente deve
ser implementada até ser confirmada pelo proprietário.

## Documentos

- [PRD.md](PRD.md): requisitos de produto e comportamento do robô.
- [STRATEGY.md](STRATEGY.md): estratégia V1 e máquina de estados.
- [ML-GOVERNANCE.md](ML-GOVERNANCE.md): Champion, Challenger, repertório e promoção.
- [PROMOTION-GATES.md](PROMOTION-GATES.md): proposta conservadora de gates de promoção.
- [FRONTEND.md](FRONTEND.md): página dinâmica de comparação e aprovação.
- [IMPLEMENTATION-PHASES.md](IMPLEMENTATION-PHASES.md): decomposição macro em duas fases.
- [PHASE-1-BACKEND-DATA-PLAN.md](PHASE-1-BACKEND-DATA-PLAN.md): execução detalhada do backend e dados.
- [PHASE-2-FRONTEND-PLAN.md](PHASE-2-FRONTEND-PLAN.md): execução detalhada do frontend.

## Decisões confirmadas

- Existe exatamente um NexusTrade por instalação.
- Donchian + ZigZag e Nexus Speed permanecem determinísticos e isolados; nenhum modelo,
  feature, campanha ou decisão do NexusTrade pode alterar seu comportamento.
- O NexusTrade não aparece como estratégia selecionável para criação de outros robôs.
- Bollinger Bands é o indicador primário obrigatório de todo Champion.
- Champion V1: Bollinger(20, 2, SMA), ADX(14), M1 e expiração de 58 segundos.
- Champion e Challenger usam exclusivamente o ativo `R_100`.
- O ADX é calculado continuamente e funciona como porta final de execução; não impede
  análise, formação ou confirmação de sinais.
- `ADX <= 22` permite a entrada; `ADX > 22` impede a ordem, mas não interrompe análise,
  coleta ou registro.
- O Challenger trabalha continuamente, independentemente do modo visual do robô.
- O botão OFF/ON e a seleção de conta controlam exclusivamente o Champion.
- Champion OFF opera continuamente em DEMO com USD 0,35 e sem gerenciamento financeiro.
- Champion ON opera na conta selecionada com o gerenciamento configurado.
- Um Trial Challenger interno e invisível ao usuário opera continuamente em DEMO com
  USD 0,35, independentemente do estado OFF/ON do Champion.
- Champion OFF e Trial Challenger compartilham a mesma conta DEMO, mas cada ordem,
  contrato, resultado e métrica pertence a uma trilha distinta.
- O aprendizado e os experimentos acontecem somente no Challenger.
- O Champion é uma versão congelada; nunca é alterado silenciosamente.
- O Challenger fecha um ciclo de avaliação a cada 24 horas.
- O ciclo fecha diariamente às 10:00 em `America/Sao_Paulo`.
- Promoção exige no mínimo 300 operações concluídas pelo Trial Challenger e ao menos
  uma semana completa de avaliação.
- A interface separa relatórios diários/semanais e análise da campanha de evolução
  acumulada.
- O Trial Challenger permanece congelado durante toda campanha de evolução, que pode
  atravessar várias semanas até a decisão humana; o laboratório cria outros candidatos
  somente em sombra.
- A semana visual vai de segunda-feira às 10:00 até a segunda seguinte às 10:00.
- Encerrar uma semana reinicia apenas os contadores da view semanal; o acumulador de
  evolução, histórico, dataset, modelos, candidatos e aprendizado não são zerados.
- O acumulador de evolução zera após `Aprovar`, `Enviar para reanálise` ou substituição
  automática do Trial na fronteira semanal.
- O laboratório pode substituir automaticamente o Trial na fronteira semanal; nesse
  caso, encerra a campanha anterior e reinicia a meta em `0/300` para a nova versão.
- Promoção exige solicitação detalhada e aprovação explícita do usuário.
- Promoção só ocorre com o NexusTrade em `OFF`, sem contrato em aberto.
- A interface recebe a nova versão sem recarregar a página.

## Situação do desenho

As definições necessárias para iniciar foram fechadas. Novos ajustes descobertos durante
a implementação devem ser registrados como decisão versionada antes de alterar os
contratos aprovados.

## Registro de decisões

| Data | Decisão |
|---|---|
| 2026-08-10 | Criar o NexusTrade como robô singleton e não como estratégia criável. |
| 2026-08-10 | Separar Champion congelado de Challenger em evolução. |
| 2026-08-10 | Manter análise contínua e usar DEMO com stake fixa no modo OFF. |
| 2026-08-10 | Exigir aprovação humana para toda promoção de Champion. |
| 2026-08-10 | Limitar o plano de implementação a três fases. |
| 2026-08-10 | OFF/ON controla somente o Champion; Trial Challenger permanece oculto e ativo. |
| 2026-08-10 | Champion OFF e Trial Challenger compartilham uma conta DEMO com contratos separados. |
| 2026-08-10 | ADX igual ou menor que 22 permite entrada; acima de 22 bloqueia. |
| 2026-08-10 | NexusTrade inteiro opera e aprende exclusivamente em R_100. |
| 2026-08-10 | Inclinação externa usa comparação simples entre duas bandas fechadas, sem tolerância. |
| 2026-08-10 | Entrada é disparada pela virada do M1, mira o primeiro segundo e não espera novo tick. |
| 2026-08-10 | Entrada ainda pode ocorrer no segundo de contingência; após 2 s o sinal é stale. |
| 2026-08-10 | Sinais coincidentes da mesma lane geram um único contrato com múltiplos motivos. |
| 2026-08-10 | ADX é gate final: acima de 22 bloqueia e consome a entrada sem parar a análise. |
| 2026-08-10 | Ciclo e relatório fecham diariamente às 10:00 no horário de Brasília. |
| 2026-08-10 | Frontend terá página própria para comparar e aprovar evolução do Champion. |
| 2026-08-10 | Proposta exige simultaneamente 300 operações concluídas e ao menos uma semana. |
| 2026-08-10 | Painel separa relatório semanal e evolução; Trial fica congelado por campanha. |
| 2026-08-10 | Virada semanal reseta somente a apresentação; aprendizado é contínuo e cumulativo. |
| 2026-08-10 | Semana de relatório começa segunda às 10h; meta de 300 acumula até decisão humana. |
| 2026-08-10 | Relatórios históricos podem ser exportados em CSV e Excel. |
| 2026-08-10 | Troca automática de Trial só ocorre na segunda às 10h e reinicia sua meta de 300. |
| 2026-08-10 | Assertividade é obrigatória em todos os relatórios, sempre acompanhada de N, wins, losses e empates; não decide promoção isoladamente. |
| 2026-08-10 | Gates conservadores aprovados como configuração inicial, sujeitos a recalibração futura versionada. |
| 2026-08-10 | Implementação dividida em duas fases: Backend/Dados e Frontend. |
| 2026-08-10 | Donchian + ZigZag e Nexus Speed permanecem determinísticos e protegidos por regressão integral. |

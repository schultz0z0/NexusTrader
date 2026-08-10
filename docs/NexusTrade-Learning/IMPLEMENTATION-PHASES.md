# Plano macro de implementação — NexusTrade Learning

Status: **duas fases aprovadas para planejamento executável**

## Invariante de isolamento

Donchian + ZigZag e Nexus Speed permanecem robôs determinísticos, com seus perfis,
estratégias, risco, execução e contratos atuais. O aprendizado de máquina existe apenas
no NexusTrade. É proibido importar modelos, features, campanhas ou decisões do
NexusTrade nas duas estratégias existentes.

O NexusTrade usa namespace, runtime, tabelas e rotas próprios. Pontos compartilhados de
infraestrutura — conexão, ownership, publicação de eventos e orquestração — só podem ser
estendidos por interfaces compatíveis. A suíte atual de Donchian/Nexus Speed deve passar
sem alteração de expectativa antes e depois de cada entrega.

## Fase 1 — Backend e dados

Entrega todo o núcleo funcional antes da interface dedicada:

- singleton protegido e provisionado automaticamente;
- runtime contínuo e isolado dos robôs comuns;
- Champion OFF/ON e Trial oculto em lanes independentes;
- estratégia Bollinger + ADX, relógio M1 e expiração de 58 segundos;
- dispatcher da conta DEMO compartilhada e limite de uma posição por lane;
- coleta 24/7 de ticks/candles e persistência de features, decisões e contratos;
- versões, campanhas, candidatos, artefatos e aprendizado contínuo;
- relatórios diários/semanais, gates, propostas, promoção e rollback;
- APIs, eventos WebSocket e exports CSV/XLSX prontos para consumo;
- testes unitários, integração, replay, Docker local, localhost, logs e smoke DEMO.

Critério de saída: todo comportamento pode ser operado e auditado pela API, reinicia sem
perder estado, não interfere em Donchian/Nexus Speed, passa toda a suíte de regressão
existente e completa uma campanha prolongada em DEMO. Não haverá validação com compra
REAL.

Plano executável: [PHASE-1-BACKEND-DATA-PLAN.md](PHASE-1-BACKEND-DATA-PLAN.md).

## Fase 2 — Frontend

Entrega a experiência completa dentro do KV atual:

- NexusTrade fixo no rail, sem criação/duplicação ou seletor de estratégia/ativo;
- controle do Champion e visualização correta dos estados OFF/ON/DEMO/REAL;
- página `Evolução do NexusTrade` com abas Relatórios e Evolução;
- comparativo diário, semanal e acumulado Champion × Trial;
- N, wins, losses, empates, assertividade, expectancy, payout, risco e gates;
- diff completo de versões, indicadores, parâmetros, features, regras e modelo;
- aprovação, reanálise, rollback, histórico semanal e exportação;
- reflexo obrigatório e em tempo real de toda transição relevante, sem F5;
- responsividade, acessibilidade e testes de contrato/renderização.

Critério de saída: o usuário consegue compreender, revisar e decidir toda evolução sem
acessar banco ou logs, e a interface nunca oculta uma transição interna relevante.

Plano executável: [PHASE-2-FRONTEND-PLAN.md](PHASE-2-FRONTEND-PLAN.md).

## Regra de passagem entre fases

A Fase 2 começa somente quando os schemas JSON e endpoints da Fase 1 estiverem
congelados por testes de contrato. Correções de backend descobertas pelo frontend podem
ser feitas, mas toda alteração de contrato exige atualizar primeiro o teste e o snapshot
de API.

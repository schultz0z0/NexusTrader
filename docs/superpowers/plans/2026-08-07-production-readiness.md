# Plano de implementação — prontidão operacional final

1. **Congelar a estratégia correta** — criar fixtures RED para 21/1/15/3, toque
   superior/inferior, deduplicação e causalidade; corrigir o analisador e contratos da
   API/UI.
2. **Persistir ownership de compras** — migrar `order_intents`, implementar estados,
   trava por conta, tracker/reconciliação Deriv e integrar ao BotSession sem retry cego.
3. **Tornar risco transacional** — migrar `risk_states`/`risk_applied`, criar transição
   pura, settlement atômico e restauração em restart.
4. **Corrigir runtime e readiness** — normalização de estado órfão, heartbeats de
   serviço/bot e endpoints live/ready com diagnósticos.
5. **Proteger REAL no servidor** — teto obrigatório, ticket efêmero vinculado à revisão,
   confirmação digitada e fluxo frontend; preservar launcher local DEMO-only.
6. **Adicionar replay/paper determinístico** — dataset versionado, clock/broker em
   memória, CLI, métricas e fixtures sem look-ahead.
7. **Endurecer produção** — comparação constante, ingress sem rota interna, usuário não
   root, filesystem/recursos/logs/health/shutdown, atualização segura de dependências.
8. **Atualizar documentação** — estado vigente, arquitetura, API, segurança,
   desenvolvimento, operações, deploy e relatório de validação.
9. **Verificar ponta a ponta** — suites Python/JS, lint sintático/dependências, Compose,
   build/health local quando disponível, inspeção de dados/logs e navegador Chrome em
   localhost sem realizar ordem REAL.

Cada item começa com teste falhando específico, recebe a menor implementação que o faz
passar e encerra com regressão da área antes do próximo item.

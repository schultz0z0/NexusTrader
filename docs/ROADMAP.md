# Roadmap

## Base concluída nesta reestruturação

- API atual Deriv com REST de contas/OTP e WebSocket autenticado.
- Reconexão supervisionada e restauração de subscriptions.
- Orquestração persistente de múltiplos robôs isolados.
- Parada segura e lifecycle idempotente de contratos.
- Conta demo obrigatória em todas as camadas de execução.
- Histórico de ticks/candles, atualização incremental e gráfico 1s/1m/5m.
- Operação aberta, marcador no gráfico, P&L, expiração e journal ao vivo.
- Dashboard de trading responsivo, API autenticada e documentação operacional.

## Próximo ciclo — qualidade de estratégia

1. Motor de backtest/replay usando a mesma interface das estratégias ao vivo.
2. Métricas: expectancy, payout mínimo, profit factor, drawdown, sequência de perdas, estabilidade por janela e regime.
3. Estratégias versionadas com schemas próprios e parâmetros imutáveis por execução.
4. Indicadores incrementais (EMA, RSI, ATR, ADX e suporte/resistência) sem recalcular todo o buffer.
5. Filtros de latência, payout, volatilidade, horário e qualidade/staleness do feed.
6. Paper trading determinístico antes de liberar cada versão em demo.

## Ciclo seguinte — operação profissional

- Calendário/scheduler por robô e limite agregado por conta.
- Portfólio de robôs com orçamento de risco compartilhado.
- Relatórios por estratégia, ativo, timeframe e versão.
- Auditoria administrativa e autenticação multiusuário/RBAC.
- Métricas Prometheus, alertas e tracing.
- PostgreSQL + Redis Streams/NATS e leasing para workers horizontais.

## Critérios antes de discutir conta real

Conta real permanece fora do escopo atual. Uma decisão futura exige revisão de segurança independente, paper/demo prolongado, backtest fora da amostra, limites agregados, kill switch testado, auditoria, observabilidade e aceite explícito do proprietário. Nenhuma performance passada elimina risco de perda.

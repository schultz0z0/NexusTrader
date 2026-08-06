# NexusTrader

Plataforma web para orquestrar robôs de opções da Deriv com isolamento por robô, proteção de banca, gráfico em tempo real e acompanhamento completo do contrato. A execução está deliberadamente limitada a contas **demo**.

## Estado atual

- Integração pela API atual da Deriv: REST para contas/OTP e WebSocket autenticado para mercado e negociação.
- Reconexão automática com restauração de assinaturas de ticks e contratos.
- Múltiplos robôs independentes, configurados e iniciados pelo dashboard.
- Gráfico de linha para 1 segundo e velas para 1/5 minutos.
- Entrada, P&L flutuante, expiração e encerramento visíveis ao vivo.
- Martingale, Soros e mão fixa; meta, stop, stake máxima, limite diário e circuit breaker.
- API e WebSocket protegidos por chave em produção.

## Início rápido

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Abra `http://127.0.0.1:8989`, informe `DASHBOARD_API_KEY` e cadastre o ID da sua conta demo. Antes de operar, veja o [guia de desenvolvimento](docs/DEVELOPMENT.md) e o [runbook de produção](docs/OPERATIONS.md).

## Documentação

- [Arquitetura](docs/ARCHITECTURE.md)
- [API do NexusTrader](docs/API.md)
- [Integração Deriv atual](docs/DERIV-API-REFERENCE.md)
- [Desenvolvimento e testes](docs/DEVELOPMENT.md)
- [Operação e deploy](docs/OPERATIONS.md)
- [Segurança](docs/SECURITY.md)

> Trading envolve risco. Indicadores e automação não garantem retorno. Valide estratégias em conta demo, com amostra suficiente e limites de perda conservadores.

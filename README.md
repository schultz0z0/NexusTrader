# NexusTrader

Plataforma web para orquestrar robôs de opções da Deriv com isolamento por robô, proteção de banca, gráfico em tempo real e acompanhamento completo do contrato. Contas **demo** e **real** são selecionáveis; execução real exige liberação explícita no servidor e confirmação adicional no painel.

> **Agentes e novos mantenedores:** comece por [Estado atual](docs/CURRENT-STATE.md).
> Ele concentra as restrições do produto, o perfil operacional vigente, pendências e o
> roteiro de validação. Planos antigos não são especificação atual.

## Estado atual

- Integração pela API atual da Deriv: REST para contas/OTP e WebSocket autenticado para mercado e negociação.
- Reconexão automática com restauração de assinaturas de ticks e contratos.
- Múltiplos robôs independentes, configurados e iniciados pelo dashboard.
- Somente Donchian+ZigZag habilitado: R_75, candles de 1 minuto e expiração de 2 minutos.
- Entrada, P&L flutuante, expiração e encerramento visíveis ao vivo, com um marcador de entrada e um de resultado por contrato.
- Martingale, Soros e mão fixa; meta, stop, stake máxima, limite diário e circuit breaker.
- API e WebSocket protegidos por chave em produção.

## Início rápido

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Abra `http://127.0.0.1:8989`, informe `DASHBOARD_API_KEY` e use o seletor global no topo para escolher uma das contas retornadas pela Deriv. A troca global é bloqueada enquanto qualquer robô estiver iniciando, rodando ou parando. Valide tudo em DEMO antes de habilitar REAL. Antes de operar, veja o [guia de desenvolvimento](docs/DEVELOPMENT.md) e o [runbook de produção](docs/OPERATIONS.md).

## Documentação

- [Estado atual e handoff para agentes](docs/CURRENT-STATE.md)
- [Auditoria vigente](docs/AUDIT-2026-08-06.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [API do NexusTrader](docs/API.md)
- [Integração Deriv atual](docs/DERIV-API-REFERENCE.md)
- [Desenvolvimento e testes](docs/DEVELOPMENT.md)
- [Operação e deploy](docs/OPERATIONS.md)
- [Segurança](docs/SECURITY.md)

> Trading envolve risco. Indicadores e automação não garantem retorno. Valide estratégias em conta demo, com amostra suficiente e limites de perda conservadores.

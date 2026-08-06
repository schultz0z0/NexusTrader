# Ambiente local da stack NexusTrader

## Objetivo

Preparar um ambiente de desenvolvimento local capaz de executar a API e o processo do bot conectados exclusivamente à conta Deriv demo. O ambiente deve permitir reproduzir falhas de sincronização, histórico, lifecycle de operações e gráfico sem alterar a estratégia Donchian+ZigZag nem permitir conta real.

## Invariantes da estratégia

- Mercado em candles de 1 minuto (`timeframe_seconds=60`).
- Donchian com período 21.
- ZigZag com depth 15, deviation 0 e backstep 3.
- Contratos com duração de 2 minutos.
- Uma posição ativa por bot.
- Nenhuma alteração nos critérios de CALL/PUT, indicadores ou gestão de stake faz parte deste trabalho.

## Arquitetura local

- API/UI: Uvicorn em `127.0.0.1:8990`.
- Persistência: SQLite isolado em `storage/nexus_trader.dev.db`.
- Bot: processo `main.py` separado, apontando para a API local e para o mesmo banco dev.
- Deriv: PAT e App ID existentes, com seleção obrigatória de conta `demo`.
- Segurança: `ALLOW_REAL_TRADING=false`; a porta permanece restrita ao loopback.

O perfil local não reutiliza o banco da VPS. A API pode ser iniciada antes do bot para validar UI, health e autenticação. O bot só inicia sessões cujo `desired_state` esteja em `RUNNING`; a configuração local será explicitamente Donchian antes do primeiro start.

## Tratamento de falhas

Os testes de desenvolvimento devem observar e registrar:

- perda/retry de eventos HTTP internos;
- restart da API com bot ativo;
- liquidação de contrato e remoção de `_active_contracts`;
- reconstrução de histórico e indicadores após reconnect/refresh;
- consistência entre SQLite, `LiveStore` e WebSocket do navegador.

Nenhuma falha deve ser contornada mudando os parâmetros da estratégia. Correções posteriores devem atuar no transporte, persistência, lifecycle ou frontend.

## Validação

- `GET /api/v1/health` retorna 200 no localhost.
- Conta real é rejeitada no backend.
- Bot local usa `strategy_id=donchian`, `R_75`, timeframe 60 e duração 2 minutos.
- Suíte Python válida e testes JavaScript passam.
- Smoke em conta demo confirma histórico, ticks, uma operação demo completa e parada limpa.


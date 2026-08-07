# Validação final — 2026-08-07

Este registro consolida a execução da fase de prontidão operacional. Ele comprova
comportamento técnico e proteções; não é promessa de rentabilidade.

## Matriz automatizada

- `127` testes Python aprovados com `python -m unittest discover -s tests`.
- `13` testes JavaScript aprovados com `node --test tests/js/*.test.mjs`.
- `compileall` aprovado para API, backtest, core, dados, banco, risco, scripts,
  estratégias e trading.
- `pip check`: `No broken requirements found`.
- checagem sintática dos módulos JavaScript aprovada.
- `docker compose config --quiet` aprovado.

O runner atual exibe um aviso de depreciação upstream do `TestClient` sobre a futura
separação `httpx2`; não há falha funcional e a suíte encerrou com código zero.

## Donchian + ZigZag

Perfil validado em testes unitários, replay e no painel:

- candle: `1m`;
- Donchian: período `21`;
- ZigZag: desvio `1%`, profundidade `15`, backstep `3`;
- expiração: `2 minutos`;
- ponta HIGH tocando Donchian Upper: `PUT`;
- ponta LOW tocando Donchian Lower: `CALL`;
- ponta histórica ou sinal já consumido: nenhuma nova entrada.

O replay usa o mesmo agregador e a mesma classe de estratégia da execução. O primeiro
tick em/depois da expiração liquida o paper trade; dataset sem esse tick produz
`INCOMPLETE`, nunca um resultado inventado.

## Chrome em localhost

Validado em `http://127.0.0.1:8990/` no Chrome real:

- autenticação do dashboard;
- conta DEMO e conta REAL presentes no seletor;
- troca DEMO → REAL e REAL → DEMO persistida com mensagem legível;
- perfil visual fixo `21 / 1 / 15 / 3`, candle `1m` e expiração `2m`;
- modal REAL exige a frase exata `REAL <account_id>`;
- com o perfil local seguro, o backend recusou o início REAL antes de proposta/compra;
- robô permaneceu `OFF`, sem posição aberta;
- zero erros ou warnings no console após a validação final;
- painel devolvido à conta DEMO.

Nenhuma ordem REAL foi enviada.

## Imagem e Compose

O stack `nexus-validation` foi reconstruído do Dockerfile final e subiu saudável:

- liveness: `{"status":"alive"}`;
- readiness: banco `ok`, nenhum bot pendente;
- processo do container: `uid=10001(nexus)`;
- root filesystem: read-only;
- capabilities: `ALL` removidas;
- `no-new-privileges=true`;
- limite de memória: `512 MiB`;
- limite de PIDs: `256`;
- API exposta apenas em `127.0.0.1:8989` no teste local.

Depois da validação, os dois containers, a rede e o volume temporário
`nexus-validation_nexus-data` foram removidos. O stack local de desenvolvimento em
`127.0.0.1:8990` permaneceu ativo.

## Limites da evidência

- Não houve deploy na VPS nesta sessão; o runbook está em `docs/OPERATIONS.md`.
- Não houve ordem REAL por segurança financeira.
- Rentabilidade exige dataset representativo, replay sem lacunas e validação DEMO
  prolongada; correção do código não elimina risco de mercado.
- A rotação do token Telegram é uma ação externa do proprietário e não foi executada.

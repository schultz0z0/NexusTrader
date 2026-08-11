# Desenvolvimento

## Ambiente local

Requisitos: Python 3.11+, Node apenas para checagem sintática opcional e Docker para reproduzir produção.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

O fluxo recomendado é o launcher seguro abaixo. A execução manual separada é útil apenas
para diagnóstico: replique todos os overrides definidos em `scripts/start_dev.ps1`, em
especial `DEV_MODE=true`, `ALLOW_REAL_TRADING=false`, `DERIV_ACCOUNT_TYPE=demo`, banco
isolado e `API_BASE_URL`. A autenticação não fica aberta por ausência acidental de
`DOMAIN`. Use somente conta DEMO.

### Stack local segura conectada à DEMO

Com o `.env` preenchido com as credenciais da Deriv, inicie API e orquestrador juntos:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1
```

O launcher abre somente `http://127.0.0.1:8990`, usa o banco isolado
`storage/nexus_trader.dev.db` e força `ALLOW_REAL_TRADING=false`. A conta REAL continua
visível e selecionável para validar a interface, mas qualquer tentativa de iniciar o robô
nessa conta é rejeitada pelo backend antes de proposta ou compra.

No painel:

1. Informe a chave local já presente no `.env`.
2. Selecione explicitamente a conta identificada como **DEMO**.
3. Confirme o único perfil suportado: **Donchian+ZigZag**, símbolo **R_75**, gráfico de **1 minuto** e expiração de **2 minutos**.
4. Inicie somente um bot e acompanhe o histórico inicial, os ticks, a abertura, as atualizações e o fechamento de uma operação demo.
5. Pare o bot e aguarde `STOPPED` antes de pressionar `Ctrl+C` no launcher.

O launcher não edita o `.env`, não seleciona conta e não inicia o bot automaticamente.

Para uma validação isolada, o launcher também aceita o arquivo de ambiente somente como
entrada e paths exclusivos. Ele importa as variáveis apenas no processo filho e aplica
depois os overrides `ALLOW_REAL_TRADING=false`, `REAL_MAX_STAKE_USD=0`,
`DERIV_ACCOUNT_TYPE=demo` e stake NexusTrade `0.35`. O bind continua restrito a
loopback. Não copie o arquivo de credenciais para o worktree.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1 `
  -EnvFile <arquivo-local> `
  -DatabasePath storage/phase1-validation/nexus.db `
  -LogsDirectory logs/phase1-validation `
  -PidDirectory storage/phase1-validation/pids `
  -RunId phase1-validation -Detached
```

Use `-ApiOnly` ou `-BotOnly` para reiniciar separadamente um componente. O modo
`-PreflightOnly` valida o contrato seguro sem iniciar processos e nunca mostra
credenciais ou paths. Encerre somente os PIDs gravados no diretório exclusivo do run;
não procure nem finalize processos por nome.

O smoke NexusTrade é somente leitura: usa exclusivamente `GET` em localhost, envia a
chave em `X-API-Key`, confirma singleton, Champion `OFF/DEMO`, Trial/Bot DEMO,
R_100/M1/58s/USD 0.35, lanes, versões, campanha, relatórios e exports. Ele nunca muda o
modo, solicita proposal ou chama compra. `--demo-only` apenas observa por uma janela de
pelo menos uma virada M1; sem sinal aprovado retorna `NO_SIGNAL`.

O transporte não segue redirects, inclusive para outro endpoint loopback, e revalida a
URL efetiva antes de ler a resposta para que `X-API-Key` nunca seja encaminhada. A
comparação de restart também recusa regressão de `snapshot_version` ou redução de
decisions, trades, reports e proposals; ambos os modos do smoke exigem Champion OFF.

```powershell
$env:ALLOW_REAL_TRADING = "false"
$env:DERIV_ACCOUNT_TYPE = "demo"
$env:DASHBOARD_API_KEY = <chave-local-em-memoria>
python -m scripts.nexus_trade_smoke --base-url http://127.0.0.1:8990
python -m scripts.nexus_trade_smoke --base-url http://127.0.0.1:8990 --demo-only
```

Para Docker, sempre escolha um projeto e porta exclusivos, force os mesmos overrides no
ambiente do comando e use um `ENV_FILE` local somente como input. O Compose não possui
`container_name`, portanto o isolamento por projeto inclui containers e volume. Finalize
com `docker compose -p <projeto> down`, sem `-v`, preservando o volume de evidência.

## Testes

```bash
python -m unittest discover -s tests -v
python -m compileall -q api backtest core data database risk strategies trading
node --check static/js/app.js
node --test tests/js/*.test.mjs
python -m pip check
```

Os testes cobrem configuração, ownership, risco persistente, replay, candles, fila, lifecycle, health, orquestração, API, Docker e frontend.

### Configuração segura do NexusTrade

O runtime isolado do NexusTrade mantém os parâmetros de segurança fixos: archive de
ticks em `storage/nexus_ticks`, stake DEMO de `0.35`, fechamento diário às `10` horas
(`America/Sao_Paulo`) e atraso máximo de entrada de `2` segundos. Não altere esses
valores por ambiente; a validação de configuração rejeita stake ou atraso diferentes e
horas fora de `0..23`.

Para importar `config.settings` durante a suíte de testes sem habilitar `DEV_MODE`,
forneça valores fictícios para `DERIV_APP_ID`, `DERIV_API_TOKEN`, `INTERNAL_API_TOKEN`
e `DASHBOARD_API_KEY` no ambiente do processo de teste. As rotas humanas de
governanca NexusTrade tambem exigem `NEXUS_HUMAN_ACTION_KEY` (segredo exclusivo,
diferente das chaves de dashboard, canal interno e REAL) e derivam a identidade
de auditoria de `NEXUS_HUMAN_ACTOR`; nenhum dos dois deve ser enviado no corpo,
registrado em logs ou exposto em snapshots.

### Replay/paper determinístico

Cada linha do dataset contém `epoch`, `quote` e opcionalmente `payout_ratio`:

```json
{"epoch":1786000000,"quote":49350.12,"payout_ratio":0.92}
```

Execute:

```bash
python -m backtest.cli --input data/replay.jsonl > replay-report.json
```

Ticks devem estar em ordem estrita e cobrir o primeiro tick em/depois da expiração de
cada posição. Caso contrário o relatório é `INCOMPLETE` e a operação é inválida, nunca
forçada como win/loss. O hash SHA-256 torna o resultado reproduzível.

Smoke test opcional e somente leitura usando as credenciais do `.env`:

```bash
python -m scripts.smoke_deriv
```

Para validar compra e settlement, use o fluxo integral pelo dashboard: ele aplica o
perfil protegido R_75/1m/2m e registra ownership, eventos e journal. Não use o modo de
compra direta do utilitário como validação da estratégia.

## Criando uma estratégia

Esta seção descreve uma extensão futura da arquitetura. No estado atual, criar, ativar
ou modificar estratégia exige autorização explícita do proprietário; Donchian+ZigZag
deve permanecer intacta.

1. Implemente `BaseStrategy` sem I/O e sem estado global.
2. Retorne `Signal` apenas depois de dados suficientes e impeça sinal duplicado no mesmo epoch.
3. Registre o ID e schema no catálogo `/api/v1/strategies`.
4. Adicione a factory em `BotSession._build_strategy`.
5. Escreva testes determinísticos com séries conhecidas, inclusive mercado lateral, tendência, gaps e dados insuficientes.
6. Valide em replay/backtest e depois em demo antes de permitir execução contínua.

Indicadores devem ser tratados como filtros probabilísticos. Para operações de 1 minuto
com expiração de 2 minutos, inclua no estudo custos/payout, latência, regime do mercado,
horário, tamanho da amostra, drawdown e risco de overfitting. Não selecione parâmetros
apenas pelo melhor resultado histórico.

## Eventos

Novos eventos devem usar `runtime_event`, ter payload serializável e identidade de robô. Eventos de mercado podem ser descartados sob pressão; eventos críticos de trade/risco/status recebem backpressure. Mantenha snapshots limitados para não transformar a API em banco de ticks.

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

Para desenvolvimento sem `DOMAIN`, as chaves podem ficar vazias. Use apenas conta demo.

```bash
python -m uvicorn api.app:app --reload --port 8000
python main.py
```

O bot publica eventos em `API_BASE_URL`; localmente o padrão é `http://127.0.0.1:8000`.

## Testes

```bash
python -m unittest discover -s tests -v
python -m compileall -q api core data database risk strategies trading
node --check static/js/app.js
```

Os testes cobrem configuração, banco, reconexão/replay, candles, fila limitada, risco, lifecycle, orquestração, API e contrato do frontend.

Smoke test opcional usando as credenciais do `.env`:

```bash
python -m scripts.smoke_deriv
python -m scripts.smoke_deriv --trade --stake 1.0  # somente demo, compra 5 ticks
```

## Criando uma estratégia

1. Implemente `BaseStrategy` sem I/O e sem estado global.
2. Retorne `Signal` apenas depois de dados suficientes e impeça sinal duplicado no mesmo epoch.
3. Registre o ID e schema no catálogo `/api/v1/strategies`.
4. Adicione a factory em `BotSession._build_strategy`.
5. Escreva testes determinísticos com séries conhecidas, inclusive mercado lateral, tendência, gaps e dados insuficientes.
6. Valide em replay/backtest e depois em demo antes de permitir execução contínua.

Indicadores devem ser tratados como filtros probabilísticos. Para operações de 1/5 minutos, inclua no estudo custos/payout, latência, regime do mercado, horário, tamanho da amostra, drawdown e risco de overfitting. Não selecione parâmetros apenas pelo melhor resultado histórico.

## Eventos

Novos eventos devem usar `runtime_event`, ter payload serializável e identidade de robô. Eventos de mercado podem ser descartados sob pressão; eventos críticos de trade/risco/status recebem backpressure. Mantenha snapshots limitados para não transformar a API em banco de ticks.

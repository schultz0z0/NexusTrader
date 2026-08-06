# Deploy VPS

O procedimento oficial foi consolidado em [docs/OPERATIONS.md](../docs/OPERATIONS.md). Este arquivo não contém tokens, IDs de conta ou credenciais reais.

Resumo:

```bash
cp .env.example .env
# preencha PAT/App ID, conta demo, DOMAIN e gere os dois segredos
docker compose config
docker compose up -d --build
curl -fsS http://127.0.0.1:8989/api/v1/health
```

Para o domínio `trade.solucoes-nexus.tech`, mantenha o proxy HTTPS apontando para o serviço `nexus-api:8000` pela rede Docker ou para `127.0.0.1:8989` no host.

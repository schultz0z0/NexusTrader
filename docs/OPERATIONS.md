# Operação e produção

## Primeira instalação

```bash
git clone <repositorio> nexus-trader
cd nexus-trader
cp .env.example .env
openssl rand -hex 32  # use para INTERNAL_API_TOKEN
openssl rand -hex 32  # use outro valor para DASHBOARD_API_KEY
docker compose config
docker compose up -d --build
docker compose ps
```

Configure `DOMAIN`, o PAT Deriv e uma conta `VRTC` demo. Nunca reutilize a chave do dashboard como token interno. O bind HTTP padrão é `127.0.0.1:8989`; o acesso público deve passar pelo proxy HTTPS.

## Validação pós-deploy

```bash
curl -fsS http://127.0.0.1:8989/api/v1/health
docker compose logs --tail=100 nexus-api
docker compose logs --tail=100 nexus-bot
```

No dashboard:

1. informe `DASHBOARD_API_KEY`;
2. confirme o selo `AMBIENTE DEMO`;
3. configure uma conta demo e salve;
4. inicie um único robô com stake mínima;
5. confirme histórico de velas/linha, ticks, estado `LIVE` e heartbeat;
6. acompanhe uma operação desde `trade.opened` até `trade.closed`;
7. pare e confirme `STOPPED` depois de eventuais contratos ativos encerrarem.

## Atualização

Solicite parada pelo painel antes do deploy. `docker compose stop nexus-bot` possui sete minutos de tolerância para concluir contratos ativos.

```bash
git pull --ff-only
docker compose build
docker compose up -d
docker compose ps
```

## Backup e restauração

O banco reside no volume `nexus-data`. Faça backup consistente com os containers parados ou usando o backup online do SQLite. Não copie apenas o `.db` enquanto arquivos WAL estão ativos sem uma estratégia consistente.

Exemplo conservador:

```bash
docker compose stop nexus-bot nexus-api
docker run --rm -v nexus-trader_nexus-data:/data -v "$PWD/backups:/backup" alpine sh -c 'cp /data/nexus_trader.db /backup/nexus_trader-$(date +%F-%H%M%S).db'
docker compose up -d
```

Teste periodicamente a restauração em outro volume.

## Observabilidade

Estados úteis: `STARTING`, `RUNNING`, `STOPPING`, `STOPPED`, `ERROR`. Investigue:

- heartbeat antigo: processo ou integração parou de progredir;
- `failed_events`: comunicação interna API/bot falhou;
- `dropped_market_events`: API lenta e fila sob pressão;
- reconexões sucessivas: rede, OTP, token ou disponibilidade Deriv;
- `risk.blocked`: limite operacional, não falha técnica.

## Incidentes

- **Gráfico sem ticks:** confirme `nexus-bot`, `API_BASE_URL=http://nexus-api:8000`, eventos internos e logs de reconexão. As assinaturas agora são restauradas automaticamente.
- **401 no painel:** confira `DASHBOARD_API_KEY`; limpe a chave salva no navegador e informe novamente.
- **401/403 Deriv:** valide PAT, App ID, escopos e se a conta demo pertence ao token.
- **Robô em STOPPING:** existe contrato aberto; não mate o processo salvo em emergência real.
- **Banco bloqueado:** confirme volume único e apenas um stack escrevendo nele; WAL e `busy_timeout` já estão habilitados.

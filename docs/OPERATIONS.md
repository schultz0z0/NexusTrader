# Operação, deploy e rollback na VPS

O domínio previsto é `trade.solucoes-nexus.tech`. A porta do host permanece em
`127.0.0.1:8989`; somente o proxy HTTPS deve ser público. Nunca use ordem REAL como
smoke test.

## 1. Preparar a primeira instalação

```bash
git clone <repositorio> nexus-trader
cd nexus-trader
cp .env.example .env
openssl rand -hex 32  # INTERNAL_API_TOKEN
openssl rand -hex 32  # DASHBOARD_API_KEY (use outro valor)
openssl rand -hex 32  # NEXUS_HUMAN_ACTION_KEY (use um terceiro valor)
nano .env
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
```

Configuração inicial segura:

```dotenv
DOMAIN=trade.solucoes-nexus.tech
NEXUS_HUMAN_ACTOR=operador-identificado
HOST_BIND=127.0.0.1
HOST_PORT=8989
API_BASE_URL=http://nexus-api:8000
ALLOW_REAL_TRADING=false
REAL_MAX_STAKE_USD=0
```

O PAT precisa dos escopos oficiais `read` e `trade`. Não reutilize PAT, chave do
dashboard ou token interno entre si.

## 2. Health e smoke DEMO

```bash
curl -fsS http://127.0.0.1:8989/api/v1/health/live
curl -fsS http://127.0.0.1:8989/api/v1/health/ready
docker compose logs --tail=100 nexus-api
docker compose logs --tail=100 nexus-bot
```

No dashboard:

1. informe `DASHBOARD_API_KEY`;
2. selecione uma conta **DEMO**;
3. confirme o cartão fixo Donchian 21, ZigZag 1/15/3, 1m e 2 minutos;
4. salve stake mínima e limites conservadores;
5. inicie um robô, confirme histórico, ticks, Donchian/ZigZag e WebSocket;
6. acompanhe `trade.opened`, `trade.updated`, eventual **AGUARDANDO LIQUIDAÇÃO** e
   somente então `trade.closed`;
7. pare e aguarde `desired_state=STOPPED` e `runtime_state=STOPPED`;
8. confirme `/health/ready` novamente e console do browser sem erros.

Se não surgir sinal naturalmente, não force uma compra. A correção do motor é validada
pelos testes/fixtures; o smoke da VPS verifica integração.

## 3. Liberar o seletor REAL

Só depois do smoke DEMO, escolha um teto que o operador realmente aceita perder por
operação:

```dotenv
ALLOW_REAL_TRADING=true
REAL_MAX_STAKE_USD=1.00
```

```bash
docker compose config --quiet
docker compose up -d --force-recreate nexus-api nexus-bot
curl -fsS http://127.0.0.1:8989/api/v1/health/ready
```

O valor `1.00` é exemplo, não recomendação financeira. REAL só inicia quando a conta
retornada pela Deriv coincide com ID/tipo persistidos, a stake não excede o teto e o
usuário digita `REAL <account_id>`. O backend emite um ticket de 60 segundos vinculado
à revisão atual e o consome uma vez. Validar seletor/modal não exige iniciar o robô.

## 4. Atualização segura

Solicite **PARAR TUDO** no painel e confirme que não há contratos `open`. Depois:

```bash
cd nexus-trader
PREVIOUS_COMMIT=$(git rev-parse HEAD)
mkdir -p backups
docker compose stop nexus-bot
# aguarde contratos abertos encerrarem e confirme o estado antes de continuar
docker compose stop nexus-api
docker run --rm -v nexus-trader_nexus-data:/data -v "$PWD/backups:/backup" alpine sh -c 'cp /data/nexus_trader.db /backup/nexus_trader-$(date +%F-%H%M%S).db'
git pull --ff-only
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8989/api/v1/health
```

O nome real do volume pode variar com `COMPOSE_PROJECT_NAME`; confirme com
`docker volume ls` antes do backup. Nunca copie apenas o `.db` com WAL ativo. Para
backup online, use a API de backup do SQLite em um procedimento dedicado.

## 5. Rollback

Se health ou smoke DEMO falhar:

```bash
docker compose stop nexus-bot nexus-api
git switch --detach "$PREVIOUS_COMMIT"
docker compose config --quiet
docker compose up -d --build
curl -fsS http://127.0.0.1:8989/api/v1/health/ready
```

Se a migração/dados também precisarem voltar, mantenha os containers parados, preserve
uma cópia forense do banco atual e restaure o arquivo confirmado do diretório
`backups/` para o volume. Teste restaurações periodicamente em outro volume.

## 6. Diagnóstico

- `health/ready` 503 com bot RUNNING: leia `checks.bots`; distingue heartbeat, Deriv,
  publisher, mercado e `ownership=quarantined`.
- Ownership em quarentena: pare o bot, consulte
  `GET /api/v1/bots/{id}/order-intents` e compare com statement/contrato na Deriv. Não
  repita `buy`.
- `STOPPING`: aguarde settlement; o grace period do bot é sete minutos.
- 401 no painel: valide `DASHBOARD_API_KEY` e a chave armazenada no browser.
- REAL bloqueado: verifique flag e teto nos dois serviços com
  `docker compose config` sem publicar a saída em logs/tickets.
- Banco bloqueado: garanta um único stack usando o volume; WAL e busy timeout já estão
  ativos.
- Emergência: `POST /api/v1/bots/stop-all` via painel persiste STOPPED e revoga tickets
  REAL; contratos já comprados continuam monitorados até settlement.

## 7. Regras operacionais permanentes

- Firewall: apenas SSH por chave e HTTPS; nunca exponha `8989`.
- Proteja `.env` e backups com permissões restritas/criptografia.
- Monitore tamanho do volume, log rotation, reinícios e readiness.
- Rode campanha DEMO e replay out-of-sample antes de considerar capital real.
- Rotacione externamente qualquer token já exposto; não o reproduza em logs ou docs.

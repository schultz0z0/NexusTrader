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

Configure `DOMAIN`, o PAT Deriv e mantenha ao menos uma conta `DOT` demo vinculada ao token. Contas `ROT` reais aparecem no mesmo seletor quando `ALLOW_REAL_TRADING=true`. Nunca reutilize a chave do dashboard como token interno. O bind HTTP padrão é `127.0.0.1:8989`; o acesso público deve passar pelo proxy HTTPS.

## Atualização da VPS atual

O domínio de produção é `trade.solucoes-nexus.tech` e já está mantido nas labels do Traefik. O `.env` da raiz foi ampliado para ser copiado para a VPS; antes de subir, abra-o com `nano .env` e preencha somente os valores vazios/secretos. Gere chaves independentes:

```bash
openssl rand -hex 32  # INTERNAL_API_TOKEN
openssl rand -hex 32  # DASHBOARD_API_KEY
nano .env
```

Confirme no arquivo:

```dotenv
DOMAIN=trade.solucoes-nexus.tech
HOST_BIND=127.0.0.1
HOST_PORT=8989
ALLOW_REAL_TRADING=true
API_BASE_URL=http://nexus-api:8000
```

`ALLOW_REAL_TRADING=true` apenas libera a escolha. A conta é selecionada globalmente no topo do painel, propagada aos robôs parados, validada novamente pela Deriv no start e confirmada outra vez antes de iniciar em REAL. A troca global fica bloqueada enquanto qualquer robô estiver iniciando, rodando ou parando.

## Validação pós-deploy

```bash
curl -fsS http://127.0.0.1:8989/api/v1/health
docker compose logs --tail=100 nexus-api
docker compose logs --tail=100 nexus-bot
```

No dashboard:

1. informe `DASHBOARD_API_KEY`;
2. confirme o selo `AMBIENTE DEMO` e a conta `DOT...`;
3. selecione uma conta demo no controle global do topo e salve a configuração do robô;
4. inicie um único robô com stake mínima;
5. confirme histórico de velas/linha, ticks, estado `LIVE` e heartbeat;
6. acompanhe uma operação desde `trade.opened` até `trade.closed`;
7. troque ativo e timeframe com o robô parado e confirme que o gráfico antigo some antes do novo stream;
8. confirme que cada contrato mostra apenas um marcador de entrada e um de saída/resultado;
9. pare e confirme `STOPPED` depois de eventuais contratos ativos encerrarem.

Somente depois desse smoke test em DEMO, selecione uma conta REAL sem saldo/stake ou mantenha o robô parado para validar a interface, o selo vermelho e o diálogo de confirmação. O procedimento de deploy não exige nem recomenda enviar uma ordem real.

## Atualização

Solicite parada pelo painel antes do deploy. `docker compose stop nexus-bot` possui sete minutos de tolerância para concluir contratos ativos.

```bash
docker compose stop nexus-bot
# aguarde contratos abertos encerrarem e confirme o estado antes de continuar
git pull --ff-only
docker compose config
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8989/api/v1/health
```

Se a VPS recebe os arquivos por cópia em vez de Git, copie o projeto e o `.env` atualizado preservando o volume `nexus-data`; então execute a mesma sequência a partir de `docker compose config`.

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
- **401/403 Deriv:** valide PAT, App ID, escopos `read`/`trade` e se as contas `DOT`/`ROT` pertencem ao token.
- **Conta REAL bloqueada:** confirme `ALLOW_REAL_TRADING=true` nos dois containers com `docker compose config`; reinicie o stack após editar o `.env`.
- **Robô em STOPPING:** existe contrato aberto; não mate o processo salvo em emergência real.
- **Banco bloqueado:** confirme volume único e apenas um stack escrevendo nele; WAL e `busy_timeout` já estão habilitados.

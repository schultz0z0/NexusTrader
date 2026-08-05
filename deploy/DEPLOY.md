# 🚀 Guia de Deploy em Produção na VPS (NexusTrader + Traefik)

Este guia fornece os passos exatos para colocar o **NexusTrader** rodando 24/7 na sua VPS Ubuntu conectando diretamente ao seu container isolado do **Traefik**.

---

## 🛠️ Pré-requisitos na VPS
1. **Docker** e **Docker Compose** instalados.
2. Container do **Traefik** em execução (com suporte a Docker provider e certificado LetsEncrypt).
3. Apontamento de DNS no seu provedor de domínio (ex: Cloudflare):
   - Tipo: `A` ou `CNAME`
   - Nome: `seu-subdominio` (ex: `derivbot`)
   - Alvo: `IP_DA_SUA_VPS`

---

## 📋 Passos para Deploy

### 1. Clonar o Repositório na VPS
Acesse a sua VPS via SSH e clone o repositório no diretório de sua preferência:

```bash
cd /opt
git clone https://github.com/seu-usuario/nexus-trader.git
cd nexus-trader
```

### 2. Configurar o Arquivo `.env`
Crie e edite o arquivo `.env`:

```bash
cp .env.example .env
nano .env
```

Preencha as variáveis de produção:
```ini
DERIV_APP_ID=341Gk2eXRU6aLeNLgKcCv
DERIV_API_TOKEN=pat_seu_token_real_ou_demo
DERIV_ACCOUNT_ID=DOT93156117

# Notificações Telegram
TELEGRAM_BOT_TOKEN=8741519735:AAH6WZThA4sCiNrawvbJdrsRZVC4yr1VGRM
TELEGRAM_CHAT_ID=6194081419

# Seu Subdomínio no Traefik (Substitua pelo seu subdomínio real!)
DOMAIN=derivbot.seudominio.com
DB_PATH=/app/data/nexus_trader.db
```

### 3. Executar o Deploy Automatizado
Dê permissão de execução e rode o script de deploy:

```bash
chmod +x deploy/deploy.sh
./deploy/deploy.sh
```

O Docker Compose vai:
1. Compilar a imagem leve do `NexusTrader`.
2. Subir os containers `nexustrader-api` e `nexustrader-bot`.
3. Montar o volume `nexus-data` em `/app/data` (preservando o banco SQLite `nexus_trader.db`).
4. Conectar a API no Traefik que emitirá automaticamente o certificado SSL (HTTPS).

---

## 🔍 Comandos de Monitoramento na VPS

- **Verificar status dos containers:**
  ```bash
  docker compose ps
  ```

- **Ver logs do Robô de Operações em tempo real:**
  ```bash
  docker compose logs -f nexus-bot
  ```

- **Ver logs da API Backend:**
  ```bash
  docker compose logs -f nexus-api
  ```

- **Reiniciar os containers:**
  ```bash
  docker compose restart
  ```

- **Parar o sistema:**
  ```bash
  docker compose down
  ```

---

## 🔒 Persistência de Dados
O banco de dados SQLite fica salvo no volume Docker `nexus-data`. Mesmo que você reinicie a VPS ou atualize o código com `git pull`, o histórico de trades, saldo e configurações configurados no site ou no Telegram **nunca serão perdidos**.

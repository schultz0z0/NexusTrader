# 🎨 Especificação Técnica: Fase 4 - API REST & Dashboard Web

Documento oficial de especificação técnica e design de interface para a **Fase 4** do sistema **NexusTrader**.

---

## 📅 Visão Geral da Fase 4

- **Objetivo:** Construir uma API REST de controle em Python (FastAPI) e uma interface Web moderna, clean e responsiva em Next.js com a paleta de cores oficial da Deriv.
- **Duração Estimada:** 4-5 semanas.
- **Resultado Esperado:** O operador poderá controlar o robô (Ligar/Desligar), ajustar regras de risco e banca em tempo real e acompanhar o PnL e gráficos pelo celular ou computador.

---

## 🎨 Design System & Estética (Paleta Oficial Deriv)

O Web App seguirá um design **premium, clean e dark mode**, inspirado no ecossistema oficial da Deriv (Deriv Trader & DBot):

### 🎨 Cores Oficiais:
- **Brand / Accent (Deriv Red):** `#FF444F` (Hover: `#E61935`)
- **Background Principal:** `#0E0E0E` / `#131722` (Deep Dark)
- **Superfícies / Cards:** `#1E222B` com bordas sutis em `#2A2E39` (Glassmorphism sutil)
- **Texto Principal:** `#FFFFFF` (Branco puro)
- **Texto Secundário:** `#C2C9D6` / `#848E9C` (Cinza suave)
- **Lucro / Win:** `#00C853` (Verde Neon Trading)
- **Prejuízo / Loss:** `#FF444F` (Coral Red)
- **Alertas / Avisos:** `#FFB300` (Amber Gold)

---

## 📐 Arquitetura da Fase 4

```
nexus-trader/
├── api/
│   ├── __init__.py
│   ├── app.py               # Instancia FastAPI + CORS + Middleware
│   ├── routes/
│   │   ├── auth.py          # JWT Auth (Login/Logout)
│   │   ├── bot_control.py   # Status, Start, Stop
│   │   ├── config.py        # Obter e Atualizar configuracoes de risco dinamicamente
│   │   └── trades.py        # Historico de operacoes e estatisticas
│   └── websocket.py         # Stream ao vivo para a interface Web
└── web/                     # Frontend Next.js / React
    ├── app/
    │   ├── layout.tsx
    │   ├── page.tsx          # Dashboard Principal
    │   ├── config/page.tsx   # Painel de Gestao de Risco
    │   └── history/page.tsx  # Historico Completo
    ├── components/
    │   ├── Header.tsx
    │   ├── StatCard.tsx
    │   ├── ChartView.tsx
    │   └── TradeTable.tsx
    └── public/
```

---

## 🛠️ API REST (FastAPI) - Endpoints

### 1. Autenticação & Sessão
- `POST /api/v1/auth/login`: Autentica o usuário e gera Token JWT.

### 2. Controle do Robô
- `GET /api/v1/bot/status`: Retorna se o bot está `RUNNING`, `PAUSED` ou `STOPPED`, estado da FSM e saldo atual.
- `POST /api/v1/bot/start`: Inicia a estratégia de trading.
- `POST /api/v1/bot/stop`: Interrompe a execução do robô de forma graciosa.

### 3. Configurações Dinâmicas de Risco & Banca
- `GET /api/v1/config/risk`: Retorna as configurações atuais de risco salvas no banco.
- `POST /api/v1/config/risk`: Atualiza dinamicamente Stop Loss, Take Profit, Max Stake, Martingale e Circuit Breaker.

### 4. Relatórios & Histórico
- `GET /api/v1/trades`: Retorna histórico paginado de operações salvas no banco de dados.
- `GET /api/v1/stats/daily`: Retorna resumo do dia (Wins, Losses, WinRate, PnL total).

### 5. Feed ao Vivo (WebSocket)
- `WS /api/v1/ws/live`: Envia ticks em tempo real, estado da estratégia, valores das Bandas de Bollinger e atualizações de PnL para renderização no gráfico.

---

## 📱 Telas do Dashboard Web (Next.js)

### 1. Dashboard Principal (Home)
- **Header:** Logo NexusTrader + Badge de Conexão Deriv + Botão Master `LIGAR BOT` / `DESLIGAR BOT` (Vermelho Deriv).
- **Cards Principais:**
  1. *PnL do Dia* (Verde/Vermelho com cifrão e variação %).
  2. *Win Rate* (% de vitórias com barra de progresso).
  3. *Total Operações* (Wins vs Losses).
  4. *Stake Atual* (Indica se está no valor base ou multiplicador Martingale).
- **Gráfico de Preço ao Vivo:** Gráfico de linha/velas com overlay das Bandas de Bollinger.
- **Tabela de Trades Recentes:** Tabela clean com hora, ativo, tipo (CALL/PUT), stake, resultado e lucro.

### 2. Painel de Configuração de Risco (Modal/Página)
- Formulário com sliders e inputs numéricos estilizados para ajustar:
  - Daily Stop Loss ($)
  - Daily Take Profit ($)
  - Stake Inicial ($)
  - Multiplicador do Martingale (ex: 2.0x)
  - Limite de Níveis do Martingale (ex: 3)
- Botão "Salvar e Aplicar Instantaneamente".

---

## 🧪 Plano de Testes da Fase 4

1. **Testes da API REST (pytest + httpx):**
   - Testar endpoints de start/stop e validação de JWT.
   - Testar atualização dinâmica de configuração de risco via POST `/api/v1/config/risk`.
2. **Testes de Integração UI -> Bot:**
   - Clicar em "LIGAR BOT" no navegador e verificar se o `main.py` passa a escutar os ticks e abrir proposals.
   - Alterar o Stop Loss pela tela e validar se o `RiskManager` assume a nova regra na mesma hora.

---

## ✅ Critérios de Aceitação da Fase 4

- [ ] API FastAPI rodando de forma assíncrona integrada ao evento loop do robô.
- [ ] Interface Web construída com Next.js, 100% responsiva (Desktop e Mobile).
- [ ] Visual alinhado com a paleta oficial da Deriv (`#FF444F`, Dark Mode `#131722`).
- [ ] Alteração de parâmetros de risco na tela reflete no robô em tempo real.
- [ ] Gráfico em tempo real atualizando via WebSocket.

# NexusTrader - Product Requirements Document (PRD)

## 1. Visão Geral do Produto
- **Nome:** NexusTrader
- **Tipo:** Trading bot automatizado com interface web
- **Plataforma alvo:** Deriv (derivativos sintéticos e *forex*)
- **Stack:** Python 3.11+ (asyncio) + FastAPI + Next.js
- **Deploy:** VPS Ubuntu (Hostinger)

O NexusTrader é uma plataforma avançada para execução automatizada de operações na corretora Deriv. Desenvolvido para suprir as lacunas das ferramentas nativas da Deriv, o sistema oferece um ambiente robusto, assíncrono e orientado a eventos para a construção, execução e monitoramento de estratégias de negociação algorítmica.

## 2. Problema a Ser Resolvido
Os traders que utilizam a Deriv atualmente dependem muito do DBot (interface baseada em XML/Blockly), que apresenta diversas limitações críticas para operações profissionais:
- Impossibilidade de realizar análise *multi-timeframe* de forma eficiente.
- Falta de ferramentas nativas para gestão de risco avançada.
- Ausência de persistência de dados estruturada para análise de *backtesting* e histórico.
- Necessidade de manter a aba do navegador aberta para o funcionamento contínuo.

O NexusTrader resolve esses problemas oferecendo:
- Operação ininterrupta (24/7) em servidor dedicado (VPS).
- Implementação de estratégias mais sofisticadas utilizando indicadores técnicos avançados em Python.
- Módulos avançados de gestão de banca e proteção de capital inteligentes e programáveis.

## 3. Público-Alvo
- Traders que operam índices sintéticos e ativos na plataforma Deriv.
- Traders que necessitam de níveis de automação avançada que vão além do que é oferecido pelo DBot.
- Usuários que desejam manter seus bots rodando continuamente 24/7 em uma VPS, sem a necessidade de manter equipamentos locais ligados.

## 4. Requisitos Funcionais

A implementação do NexusTrader será dividida em 6 fases incrementais:

### Fase 1 - Core Engine
- **RF-001:** Conexão *WebSocket* com a Deriv API (endpoint: `wss://ws.derivws.com/websockets/v3`).
- **RF-002:** Autenticação via API Token utilizando a chamada de `authorize`.
- **RF-003:** Recepção de dados de mercado em tempo real (fluxo contínuo de *ticks* e *candles*).
- **RF-004:** Envio de requisições e propostas de contrato (`proposal`).
- **RF-005:** Compra efetiva de contratos (`buy`).
- **RF-006:** Monitoramento constante de contratos abertos (`proposal_open_contract`).
- **RF-007:** Leitura precisa de resultados (*win*/*loss*) ao fechamento da operação.
- **RF-008:** Controle inteligente de fluxo e tomada de decisão para `trade_again`.
- **RF-009:** Consulta em tempo real de saldo da conta (`balance`).
- **RF-010:** Consulta de portfólio e histórico de posições (`portfolio`).
- **RF-011:** Implementação de sistema de *logging* estruturado com múltiplos níveis (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
- **RF-012:** Configuração dinâmica de parâmetros através de arquivo `.env`.
- **RF-013:** Mecanismo de reconexão automática em caso de queda da conexão de rede ou do *WebSocket*.

### Fase 2 - Estratégias
- **RF-014:** Motor de estratégias plugável, permitindo o carregamento de múltiplas lógicas de *trading*.
- **RF-015:** Biblioteca de indicadores técnicos nativos (ex: *Bollinger Bands* (BB), RSI, SMA, EMA, MACD).
- **RF-016:** Gestão de banca modular (ex: Soros, Martingale, Mão Fixa, Masaniello).
- **RF-017:** Ferramenta de *backtesting* com injeção de dados históricos da Deriv.

### Fase 3 - Risk Management (Gestão de Risco)
- **RF-018:** Sistema dinâmico de *Stop Loss* (SL) e *Take Profit* (TP).
- **RF-019:** *Circuit breaker* (desligamento emergencial) baseado em perdas consecutivas ou volatilidade extrema.
- **RF-020:** Definição e aplicação de limites diários estritos de operação (lucro ou perda máxima).
- **RF-021:** Monitoramento de saúde e integridade do bot (*healthcheck* contínuo).

### Fase 4 - Dashboard
- **RF-022:** Criação de API REST de controle (construída em FastAPI) para manipulação do bot.
- **RF-023:** Desenvolvimento de um *dashboard* web intuitivo com autenticação segura.
- **RF-024:** Renderização visual de gráficos de performance e estatísticas de uso.
- **RF-025:** Apresentação clara do histórico e detalhamento das operações (*trades*).

### Fase 5 - Integrações
- **RF-026:** Sistema de notificações via Telegram para alertas operacionais e resumos de performance.
- **RF-027:** Geração e envio de relatórios automáticos (diários/semanais).

### Fase 6 - Multi-conta
- **RF-028:** Suporte para gerenciamento de múltiplas contas Deriv simultaneamente.
- **RF-029:** Módulo integrado de *copy trading* para espelhar as execuções em contas secundárias.
- **RF-030:** Esteira completa de integração e entrega contínua (CI/CD) do projeto.

## 5. Requisitos Não-Funcionais
- **RNF-001:** Latência máxima admitida de 500ms entre a geração de um sinal e a execução da ordem.
- **RNF-002:** Disponibilidade garantida de 99.5% do serviço (considerando janelas de manutenção planejadas).
- **RNF-003:** Sistema de reconexão automática capaz de restabelecer o link com a Deriv API em menos de 5 segundos.
- **RNF-004:** Retenção e rotação de logs estruturados por pelo menos 90 dias, para fins de auditoria.
- **RNF-005:** Segurança rígida de segredos: Tokens de API da Deriv nunca devem ser comitados no controle de versão do repositório.
- **RNF-006:** Otimização de recursos computacionais: memória máxima de 512MB alocada por instância ativa do bot.
- **RNF-007:** Compatibilidade técnica exclusiva com Python 3.11 ou versões superiores.
- **RNF-008:** Garantia de operação fluída no sistema operacional Ubuntu 22.04 LTS.

## 6. Arquitetura de Alto Nível

Abaixo apresentamos a arquitetura proposta para a solução, demonstrando a topologia dos serviços, o fluxo de comunicação entre os módulos e o ciclo de vida do *State Machine*.

```mermaid
flowchart TD
    User([Usuário])
    DerivAPI([Deriv API WebSocket])
    Telegram([Telegram API])
    
    subgraph VPS["VPS Ubuntu (Hostinger)"]
        
        subgraph Frontend["Dashboard Web (Next.js)"]
            UI[Interface de Usuário]
        end
        
        subgraph Backend["FastAPI REST API (Fase 4)"]
            API[Control API]
        end

        subgraph Database["Armazenamento"]
            DB[(SQLite/PostgreSQL)]
        end
        
        subgraph Core["NexusTrader Core (Python asyncio)"]
            WS[WebSocket Client]
            Data[Data Handler\n(ticks buffer, candle aggregation)]
            Engine[Strategy Engine\n(plugável)]
            Risk[Risk Manager]
            Exec[Order Executor]
            
            subgraph SM["State Machine"]
                IDLE((IDLE)) --> ANALYZING((ANALYZING))
                ANALYZING -->|Sinal de Entrada| PURCHASING((PURCHASING))
                PURCHASING --> MONITORING((MONITORING))
                MONITORING -->|Contrato Fechado| RESULT((RESULT))
                RESULT --> IDLE
            end
        end
    end

    User <-->|Acessa| UI
    User <-->|Recebe Notificações| Telegram
    
    UI <-->|REST / WS| API
    API <--> Core
    API --> DB
    Core --> DB
    Core -->|Notifica| Telegram
    
    WS <-->|WSS| DerivAPI
    WS --> Data
    Data --> Engine
    Engine --> Risk
    Risk --> Exec
    Exec -->|Envia Ordem| WS
    SM -.- Engine
```

## 7. Modelo de Dados Inicial

O sistema de armazenamento persistirá as transações e o estado da aplicação baseando-se nas seguintes entidades relacionais:

### Tabela `trades`
Responsável por armazenar cada operação de negociação efetuada.
- `id`: UUID ou Inteiro Auto-incremental (Chave Primária)
- `strategy`: Nome da estratégia utilizada (Ex: "RSI_Oversold")
- `symbol`: Ativo negociado (Ex: "R_100")
- `contract_type`: Tipo do contrato (Ex: "CALL", "PUT")
- `entry_price`: Preço de entrada na operação (Float)
- `exit_price`: Preço de saída na operação (Float)
- `stake`: Valor de capital alocado na operação (Float)
- `payout`: Valor do prêmio (se ganhar)
- `profit`: Resultado numérico final, lucro ou prejuízo da operação (Float)
- `result`: Enum Status final ("WIN", "LOSS", "VOID")
- `timestamp`: Data e hora da operação (DateTime)

### Tabela `sessions`
Responsável por registrar ciclos/sessões de operação do bot, facilitando análises agregadas.
- `id`: Identificador da sessão (Chave Primária)
- `start_time`: Data e hora de início da sessão (DateTime)
- `end_time`: Data e hora de finalização da sessão (DateTime, Nullable)
- `total_trades`: Quantidade total de operações na sessão (Integer)
- `wins`: Total de operações com sucesso (Integer)
- `losses`: Total de operações com falha/prejuízo (Integer)
- `net_profit`: Lucro líquido consolidado da sessão (Float)

### Tabela `strategies`
Responsável por persistir configurações dinâmicas de instâncias de estratégias.
- `id`: Identificador da estratégia (Chave Primária)
- `name`: Identificação textual (Ex: "Moving Average Crossover")
- `parameters_json`: Configurações em formato JSON para máxima flexibilidade (JSON)
- `is_active`: Indicador se a estratégia está ligada ou desligada (Boolean)

### Tabela `account_snapshots`
Responsável por tirar fotografias (*snapshots*) temporais do saldo da conta, permitindo traçar curvas de patrimônio.
- `id`: Identificador (Chave Primária)
- `balance`: Valor do saldo registrado no momento da fotografia (Float)
- `timestamp`: Momento exato da captura (DateTime)

## 8. Deriv API - Referência Técnica

Esta seção apresenta um mapeamento preliminar das principais interações *WebSocket* necessárias para orquestrar o NexusTrader.

| Categoria | Chamada (Command) | Descrição do Fluxo/Ação | Fase |
|-----------|------------------|---------------------------|------|
| Conexão | `ping` | *Heartbeat* do sistema para manter a conexão ativa e checar latência. | 1 |
| Conexão | `time` | Sincronização da hora do servidor da corretora. | 1 |
| Autenticação| `authorize` | Processo de login e validação do token da API de acesso. | 1 |
| Mercado | `active_symbols` | Listar dinamicamente os ativos (sintéticos, forex, etc.) ativos. | 1 |
| Mercado | `contracts_for` | Analisar as condições e contratos disponíveis de um ativo específico. | 1 |
| Mercado | `ticks` | Assinatura (*subscription*) para recebimento do *stream* de preços atual. | 1 |
| Mercado | `ticks_history` | Extração do histórico de dados passados (OHLC ou Ticks isolados) para gráficos e indicadores. | 1 |
| Trading | `proposal` | Solicitação do prêmio (*quote*) exato para um contrato pretendido antes da execução. | 1 |
| Trading | `buy` | Ação efetiva de comprar (abrir) o contrato com a corretora. | 1 |
| Trading | `sell` | Ação de vender/liquidar um contrato antes de seu vencimento natural. | 1 |
| Trading | `proposal_open_contract` | Assinatura contínua para monitoramento da vida de um contrato recém-adquirido. | 1 |
| Conta | `balance` | Requisição/subscrição para leitura em tempo real do saldo em carteira. | 1 |
| Conta | `portfolio` | Consulta agregada sobre todas as posições atualmente em aberto. | 1 |
| Conta | `profit_table` | Leitura do histórico sumarizado de lucros do período. | 2 |
| Conta | `statement` | Extrato consolidado de todas as transações financeiras. | 2 |
| Sistema | `forget` | Cancelar uma subscrição *streaming* específica, desocupando banda de rede. | 1 |
| Sistema | `forget_all` | Fechar agressivamente grupos de *streams* (ex: esquecer todos os ticks assinados). | 1 |

## 9. Glossário

- **API Token:** Uma chave criptográfica gerada pela plataforma da corretora (Deriv) que permite a sistemas de terceiros (como o NexusTrader) se autenticarem e agirem em nome da conta do usuário.
- **Backtesting:** O processo de validar uma hipótese e lógica de *trading* utilizando dados ocorridos no passado, verificando se ela teria sido rentável ou não.
- **Bot / Trading Bot:** Um software ou robô que monitora cotações e toma decisões de negociação de forma automatizada através de regras matemáticas.
- **Candle / OHLC:** Representação gráfica de um período de tempo (*timeframe*), simbolizando os valores de Abertura (*Open*), Máxima (*High*), Mínima (*Low*) e Fechamento (*Close*) de um ativo.
- **Circuit Breaker:** Mecanismo de defesa do Risk Manager projetado para suspender o bot instantaneamente se as condições de mercado estiverem muito voláteis ou após atingir limites graves de perda.
- **Copy Trading:** Uma funcionalidade que espelha exatamente as decisões e entradas que uma conta principal (Master) tomou e as replica em outras contas (Slaves).
- **Martingale:** Sistema tradicional de gestão de banca de alto risco onde a aposta ou *stake* aumenta de proporção (ex: dobram-se os valores) após uma perda, visando cobrir o prejuízo na próxima vitória.
- **Soros / Anti-Martingale:** Sistema de capitalização geométrica e progressiva que reinveste os próprios lucros das transações anteriores em novas operações.
- **State Machine (Máquina de Estados):** Um modelo de padrão de projeto arquitetural lógico onde o bot transita entre fases operacionais rígidas e estritas, nunca efetuando operações inválidas em uma ordem errada.
- **Stop Loss (SL) / Take Profit (TP):** Níveis parametrizados para fechar uma posição ou travar o funcionamento. SL significa o limite que aceita-se perder e o TP a região ou montante pré-programado que o *trader* decide embolsar.
- **Tick:** O menor movimento possível no preço de um contrato/ativo enviado continuamente pela *Exchange*.
- **VPS (Virtual Private Server):** Servidor em nuvem (ex: Hostinger Ubuntu) operando initerruptamente para abrigar a aplicação, assegurando menor latência e ausência das restrições de uma máquina local.

## 10. Riscos e Mitigações

A seguir apresentamos a matriz de riscos identificados nas fases de desenvolvimento e operação em ambiente real e as suas respectivas estratégias mitigadoras.

| ID Risco | Descrição do Risco | Probabilidade | Impacto | Estratégia de Mitigação |
|----------|--------------------|--------------|---------|--------------------------|
| **R-01** | Queda abrupta da conexão *WebSocket* prejudicando o monitoramento de uma operação aberta. | Média | Alto | Implementação mandatória do requisito RNF-003, que forçará reconexão e re-assinatura (*re-subscription*) de todos os contratos abertos no `proposal_open_contract` dentro de no máximo 5s. |
| **R-02** | Vazamento do *API Token* via logs, *commits* git indevidos, ou endpoints REST expostos. | Baixa | Crítico | Aplicação rigorosa das variáveis de ambiente via arquivo `.env`, inclusão no `.gitignore`, anonimização nos relatórios de log (RNF-005) e proteção da REST API via autenticação JWT robusta. |
| **R-03** | Consumo exponencial de memória por acúmulo infinito do histórico de *ticks/candles* no *Data Handler*. | Alta | Alto | Controle programático rígido e limpeza temporal (*garbage collection* periódico) das estruturas de cache baseadas no tamanho das listas limitadas. Envio periódico de histórico para o DB e adoção do limite de memória por instância estipulado em RNF-006 (512MB). |
| **R-04** | Oscilação aguda de volatilidade causando sequências devastadoras de *Losses*. | Alta | Alto | Emprego compulsório dos módulos do *Risk Manager*, notadamente o *Circuit Breaker* global que interceptará operações se os Limites Diários (*Daily Drawdown*) forem ultrapassados, congelando a *State Machine*. |
| **R-05** | Latência excessiva no VPS (maior do que 500ms) resultando em rejeições sistemáticas e perdas de oportunidade por parte da Deriv API. | Baixa | Moderado | Escolher zonas geográficas (*regions*) adequadas do VPS da Hostinger com rotas e conectividade mais estáveis com o tráfego da API da Deriv; *healthchecks* diários medindo tempos médios de resposta de ping. |
| **R-06** | Mudanças bruscas, ou introdução de *breaking changes*, nas especificações e endpoints da Deriv API v3, paralisando o núcleo de comunicação. | Baixa | Alto | Adoção rigorosa da arquitetura de adaptadores, encapsulando todas as requisições *WebSocket* no submódulo de *Core/WS*. Acompanhamento constante da documentação e lista de discussão de desenvolvedores da Deriv. |

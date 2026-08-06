# Integração com a API atual da Deriv

Referência de implementação baseada na [documentação atual da Deriv](https://developers.deriv.com/docs/). O NexusTrader não usa o fluxo legado de `authorize` com token enviado pelo WebSocket.

## Autenticação implementada

1. O PAT é enviado por HTTPS com `Authorization: Bearer` e `Deriv-App-ID`.
2. `GET /trading/v1/options/accounts` lista as contas disponíveis.
3. `POST /trading/v1/options/accounts/{accountId}/otp` retorna uma URL WebSocket pré-autenticada e temporária.
4. A URL é consumida uma única vez pela conexão daquela conta.
5. Toda reconexão solicita um novo OTP para a mesma `account_id` e restaura as assinaturas registradas.

Esse encadeamento elimina o erro anterior no qual uma URL OTP era obtida, descartada e substituída por outra baseada na conta global.

## Mensagens WebSocket usadas

| Mensagem | Uso |
|---|---|
| `ticks_history` + `style=ticks` | Linha histórica para índice de 1 segundo |
| `ticks_history` + `style=candles` + `granularity` | OHLC de 60 ou 300 segundos |
| `ticks` + `subscribe=1` | Stream de preço ao vivo |
| `proposal` | Cotação do contrato antes da compra |
| `buy` | Compra somente depois de nova checagem demo-only |
| `proposal_open_contract` + `subscribe=1` | P&L, spot e status até `is_sold=1` |
| `portfolio` | Reconciliação de contratos persistidos como abertos após crash |
| `forget` | Cancelamento de uma assinatura remota |
| `ping` | Heartbeat da conexão |

Requisições são correlacionadas por `req_id`. Assinaturas possuem chaves estáveis locais (`ticks:{bot}:{symbol}` e `contract:{id}`), independentes do ID remoto que muda após reconectar.

## Contratos e durações

O modelo atual envia `CALL` ou `PUT`, `basis=stake`, moeda USD e o ativo em `underlying_symbol`. A duração é configurável e não deve ser confundida com o timeframe visual:

- `duration_unit=t`: expiração em ticks;
- `duration_unit=m`: expiração em minutos;
- gráfico 1s: linha;
- gráfico 60s/300s: velas OHLC.

Antes de adicionar outro tipo de contrato, confirme disponibilidade e parâmetros no [API Explorer oficial](https://developers.deriv.com/).

## Segurança operacional

`account_type=real` é rejeitado tanto pela API de configuração quanto pela sessão e pelo executor imediatamente antes de `buy`. Essa defesa em profundidade evita que uma alteração isolada de interface habilite capital real.

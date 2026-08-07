# Reconciliação de settlement e estado visual do contrato

Data: 2026-08-06

## Contexto

O NexusTrader acompanha cada contrato por uma assinatura autenticada de
`proposal_open_contract`. A liquidação só é persistida quando a Deriv envia um payload
com `is_sold=1`. Esse critério é correto, mas a entrega do update terminal não é
garantida pela assinatura observada: no incidente reproduzido em localhost, a Deriv já
respondia `is_sold=1`, `status=lost` e `profit=-50.00` em uma consulta pontual, enquanto
o último evento recebido pela sessão e o registro SQLite continuavam `open`.

O frontend não criou a inconsistência. `LiveStore` e browser mantiveram corretamente o
último estado publicado pelo backend. A lacuna está entre a liquidação confirmada pela
Deriv e a ausência de reconciliação ativa durante uma sessão que permanece em execução.
Hoje essa reconciliação ocorre apenas após restart, quando contratos locais abertos são
resubscritos.

## Objetivos

- Confirmar settlement mesmo quando o update terminal da assinatura não chegar.
- Preservar a Deriv como fonte de verdade: somente `is_sold=1` fecha um contrato.
- Aplicar settlement uma única vez mesmo com respostas duplicadas ou concorrentes.
- Manter persistência, gestão de stake, circuit breaker e publicação do fechamento na
  ordem segura já adotada.
- Exibir um estado honesto no frontend entre expiração e liquidação.
- Usar o mesmo mecanismo para contratos novos e recuperados após restart.

## Fora do escopo

- Não alterar conceito, sinais ou parâmetros de Donchian+ZigZag.
- Não resolver nesta fase compras aceitas cuja resposta de `buy` se perdeu antes de o
  `contract_id` ser persistido.
- Não persistir nesta fase o estado de Martingale, Soros ou circuit breaker.
- Não habilitar, testar ou executar conta REAL.
- Não alterar a semântica de lucro antes de a Deriv confirmar `is_sold=1`.

## Abordagens consideradas

### Limpeza local por cronômetro

O browser poderia remover o card quando a expiração chegasse a zero. Essa opção foi
rejeitada porque expiração e liquidação não são equivalentes. Ela esconderia posições
ainda não vendidas, contabilizaria resultados sem confirmação ou produziria divergência
entre frontend, SQLite e Deriv.

### Varredor central de trades no banco

Um processo central poderia consultar periodicamente todos os trades `open`. Embora
funcional, ele duplicaria ownership de conexão, callbacks e lifecycle já pertencentes a
`BotSession`. Em um ambiente multi-robô, também exigiria coordenação adicional por conta
para não competir com as sessões existentes.

### Assinatura com reconciliação ativa por contrato

Esta é a abordagem aprovada. `ContractMonitor` continua responsável pelo contrato e
combina duas fontes da mesma conexão autenticada:

1. assinatura de `proposal_open_contract` para atualizações de baixa latência;
2. consultas pontuais do mesmo endpoint após a expiração, até receber `is_sold=1`.

As duas fontes convergem no mesmo handler idempotente. Não existe caminho separado de
settlement nem inferência local de resultado.

## Arquitetura

### ContractMonitor

`trading/monitor.py` passa a manter uma tarefa de reconciliação por `contract_id`, além
da assinatura existente. O monitor armazena:

- contratos já liquidados;
- locks de settlement por contrato;
- tarefas de reconciliação ativas;
- último `date_expiry` conhecido;
- sinal de encerramento do monitor.

O callback da assinatura e a resposta da consulta pontual chamam uma única rotina de
processamento. Essa rotina segue as regras:

1. payload sem `proposal_open_contract` ou sem o `contract_id` esperado é ignorado;
2. enquanto `is_sold != 1`, publica apenas atualização aberta;
3. se `is_expired=1` e ainda não foi vendido, a atualização recebe
   `lifecycle_state=awaiting_settlement`;
4. com `is_sold=1`, entra no lock do contrato e verifica idempotência;
5. executa o callback de settlement;
6. somente depois do callback bem-sucedido marca o contrato como processado, cancela a
   reconciliação e remove a assinatura.

Se persistência ou publicação falharem, o contrato não entra no conjunto de liquidados.
A próxima atualização ou consulta poderá reaplicar o mesmo settlement.

### Agendamento da reconciliação

As configurações novas são:

- `CONTRACT_RECONCILE_INTERVAL_SECONDS=5`;
- `CONTRACT_EXPIRY_GRACE_SECONDS=1`.

Quando o primeiro payload informa `date_expiry`, a tarefa aguarda até
`date_expiry + CONTRACT_EXPIRY_GRACE_SECONDS`. Se o prazo já passou, consulta
imediatamente. Se nenhum payload informar expiração, a primeira consulta ocorre após
`CONTRACT_RECONCILE_INTERVAL_SECONDS`, garantindo progresso mesmo com resposta inicial
incompleta.

Enquanto `is_sold != 1`, erros `Timeout`, `Disconnected`, `SendError` e respostas ainda
abertas aguardam o intervalo configurado e tentam novamente. Não há retry de `buy` e não
há limite que transforme ausência de resposta em fechamento. O loop termina apenas com
settlement confirmado ou `ContractMonitor.close()`.

`CONTRACT_RECONCILE_INTERVAL_SECONDS` deve ser pelo menos 1 segundo e
`CONTRACT_EXPIRY_GRACE_SECONDS` não pode ser negativo.

### Integração com BotSession

`core/bot_session.py` continua sendo o owner de estratégia, risco e journal. Para um
payload aberto, publica `trade.updated` com `status=open` e `lifecycle_state` derivado.
Para um payload vendido:

1. cria o trade final com `status=closed`;
2. executa `repository.upsert_trade`;
3. aplica `strategy.on_trade_result`;
4. aplica `circuit_breaker.record_result`;
5. remove o contrato de `_active_contracts`;
6. publica `trade.closed`.

O monitor é fechado no `finally` da sessão antes da conexão. Isso cancela tarefas
pendentes sem deixá-las acessar um socket encerrado. Contratos recuperados por
`_recover_owned_contracts` passam pela mesma assinatura e pela mesma reconciliação.

### Estado publicado

O payload do trade passa a incluir `lifecycle_state`:

- `live`: contrato aberto antes da expiração;
- `awaiting_settlement`: expirado ou com tempo esgotado, mas sem `is_sold=1`;
- `closed`: liquidação confirmada.

`status` permanece `open` ou `closed` para manter o contrato persistente atual. O novo
campo é telemetria do lifecycle e não cria um terceiro estado contábil.

Campos de telemetria terminal disponibilizados pela Deriv, como `is_sold`, `is_expired`
e `date_settlement`, podem ser preservados no evento, mas nenhum deles substitui
`is_sold=1` como gate do fechamento.

## Frontend

Um módulo puro `static/js/trade_state.js` calcula a apresentação a partir do trade e do
epoch atual. Isso permite testes sem carregar todo o DOM.

Antes da expiração:

- chip `AO VIVO`;
- título `P&L FLUTUANTE`;
- countdown regressivo.

Depois da expiração, enquanto o backend ainda mantém `status=open`:

- chip `AGUARDANDO`;
- texto auxiliar `LIQUIDAÇÃO DERIV`;
- countdown substituído por `Aguardando liquidação`;
- o valor permanece provisório e não entra no journal nem nas métricas.

Quando `trade.closed` chega, o comportamento atual é preservado: limpar o card, fechar o
marcador no gráfico, inserir a operação no journal, atualizar métricas e consultar o
saldo novamente.

O frontend nunca converte sozinho um contrato expirado em ganho ou perda.

## Falhas e concorrência

- Assinatura e consulta podem receber o mesmo settlement. O lock e o conjunto de IDs
  liquidados garantem uma aplicação.
- A consulta pode responder antes do callback da assinatura. Ambas aguardam o mesmo
  lock.
- Se o callback falhar, o settlement continua elegível para retry.
- Se a conexão cair, o loop aguarda e consulta novamente depois da reconexão; não abre
  outra posição enquanto `_active_contracts` contém o contrato.
- Ao parar o robô, contratos ativos continuam sendo reconciliados até liquidação ou até
  o timeout operacional já existente. O timeout pode colocar a sessão em erro, mas não
  falsifica fechamento.
- Ao encerrar a sessão, tarefas e assinaturas são canceladas de forma idempotente.

## Testes

### Python

Adicionar testes em `tests/test_trade_lifecycle.py` para:

- settlement recebido somente pela assinatura;
- assinatura permanecer aberta e consulta pontual retornar `is_sold=1`;
- consulta retornar `is_expired=1`, `is_sold=0` e continuar reconciliando;
- settlement duplicado entre assinatura e consulta ser aplicado uma vez;
- falha do callback permitir retry posterior;
- ausência de `date_expiry` iniciar fallback após o intervalo;
- `close()` cancelar tarefas e assinaturas sem vazamento;
- contrato recuperado já liquidado convergir para `closed` sem nova compra.

Adicionar testes de configuração em `tests/test_settings.py` para os limites dos dois
novos parâmetros.

### JavaScript

Adicionar `tests/js/trade_state.test.mjs` para:

- contrato antes da expiração renderizar estado live e countdown;
- contrato expirado ainda aberto renderizar awaiting settlement;
- `lifecycle_state=awaiting_settlement` prevalecer quando o relógio local divergir;
- contrato fechado não ser apresentado como ativo;
- nenhum estado provisório ser contado como operação encerrada.

### Regressão completa

Executar:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q api core data database risk strategies trading
node --test tests/js/*.test.mjs
.\.venv\Scripts\python.exe -m pip check
git diff --check
git diff --exit-code -- strategies/donchian_zigzag.py utils/indicators.py
```

## Validação em localhost/DEMO

Após os testes, reiniciar API e orquestrador pelo launcher local. O contrato atualmente
preso deve ser lido do SQLite, consultado na Deriv e persistido como `closed/lost`, sem
nova compra durante a recuperação. O dashboard deve remover o card, inserir a perda de
US$ 50,00 no journal e atualizar saldo e métricas.

Em uma operação DEMO posterior, observar dois caminhos:

1. settlement normal pela assinatura;
2. atraso entre expiração e settlement, exibindo `AGUARDANDO LIQUIDAÇÃO` até o evento
   terminal.

Não será provocada compra direta, conta REAL ou alteração de estratégia para validar a
fase.

## Critérios de aceite

- Nenhum trade permanece `open` quando uma consulta autenticada já retorna `is_sold=1`.
- Nenhum trade é fechado antes de `is_sold=1`.
- Settlement duplicado produz uma única linha e uma única atualização de risco.
- O frontend diferencia posição live de posição aguardando liquidação.
- Restart durante um contrato preserva ownership e converge para o estado terminal.
- Todos os testes passam e a estratégia protegida permanece sem diff.

## Fases seguintes

Esta entrega cria a base de lifecycle necessária, mas não antecipa os projetos já
separados e aprovados:

1. intents duráveis, transaction stream e ownership de compras incertas;
2. persistência de Martingale, Soros e circuit breaker;
3. health operacional, usuário não-root, limites e rotação de logs;
4. replay tick-level, paper trading determinístico e métricas profissionais.

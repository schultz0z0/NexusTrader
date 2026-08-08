# Nexus Speed ADX Threshold Selector Design

## Objetivo

Permitir que cada bot Nexus Speed use um limiar mínimo de ADX escolhido pelo usuário entre 20, 25 e 30. O valor padrão permanece 30 e só passa a valer depois que a configuração é salva e o bot é iniciado novamente.

## Abordagem aprovada

Usar um seletor fechado no formulário do Nexus Speed. As opções são:

- 20: flexível, com maior frequência e maior exposição a ruído;
- 25: intermediário;
- 30: conservador e padrão.

Um campo numérico livre foi descartado porque permitiria valores fora do perfil validado. Uma configuração global foi descartada porque impediria que bots Nexus diferentes usem limiares próprios.

## Fluxo de configuração

Ao criar um bot Nexus Speed, o formulário inicia com ADX 30. Ao editar um bot existente, o seletor mostra o valor persistido; configurações antigas sem valor explícito são normalizadas para 30. Salvar persiste o valor em `strategy_config.adx_threshold`. Alterar o seletor não muda uma sessão já em execução: o runtime lê a configuração persistida quando o bot é iniciado.

## Validação e runtime

A API aceita somente os valores inteiros 20, 25 e 30 para `adx_threshold`. Todos os demais parâmetros Nexus Speed continuam fixos e protegidos contra deriva. O construtor da estratégia recebe o limiar validado e mantém a regra estrita `ADX > limiar`; igualdade não aprova o candle.

O replay/backtest usa o mesmo valor persistido para reproduzir a decisão do runtime. O valor escolhido não altera direção, inclinação da EMA, distância de 0,30 ATR, confirmação no tick seguinte, expiração de 5 ticks ou retorno líquido mínimo de 87%.

## Interface

O campo somente leitura `Filtro` será substituído por um seletor rotulado `Força mínima (ADX)`. A distância mínima de 0,30 ATR continuará visível separadamente. O seletor só aparece no perfil Nexus Speed e oferece exatamente 20, 25 e 30.

## Compatibilidade e segurança

Bots existentes continuam com ADX 30. A conta demo e o bloqueio de trading real permanecem inalterados. Valores adulterados fora da whitelist são rejeitados pela API, mesmo que enviados sem usar a interface.

## Testes de aceitação

- Novo Nexus Speed é salvo com ADX 30 por padrão.
- O formulário permite selecionar e restaurar 20, 25 ou 30.
- A API aceita os três valores e rejeita qualquer outro.
- A persistência preserva o valor escolhido.
- O runtime aplica o valor ao criar a estratégia.
- O replay utiliza o mesmo limiar.
- Os demais parâmetros fixos e as travas demo permanecem intactos.

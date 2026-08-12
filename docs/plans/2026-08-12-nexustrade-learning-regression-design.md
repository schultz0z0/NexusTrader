# NexusTrade Learning Regression Fix Design

**Data:** 2026-08-12

## Objetivo

Fechar as regressões reais hoje reproduzíveis no `NexusTrade Learning`, com foco em
duas áreas: scheduler/laboratório de aprendizado e recuperação causal do arquivo de
ticks. O ciclo deve terminar com validação automatizada renovada, checagem manual em
navegador e documentação incremental alinhada ao estado efetivo do código.

## Escopo aprovado

- Corrigir primeiro as falhas de `learning_lab`.
- Corrigir depois as falhas de `tick_archive`.
- Manter o menor diff possível.
- Validar continuamente com TDD e regressões focadas.
- Atualizar documentação de validação/runbook conforme cada correção for confirmada.
- Fechar com teste local em navegador e checklist operacional para a VPS.

## Problemas confirmados

### 1. Scheduler/laboratório

Quatro testes falham em `tests/test_nexus_trade_learning_lab.py` com:

- `ValueError: now cannot precede the durable scheduler cursor`

O sintoma indica desacordo entre o cursor persistido do laboratório e a fronteira
temporal entregue ao scheduler. Isso conflita com o contrato documental que exige jobs
idempotentes, recuperáveis após restart e preservação das fronteiras de 10:00 BRT.

### 2. Tick archive/restart causal

Três testes falham em `tests/test_nexus_trade_tick_archive.py` com:

- `ValueError: manifest diverges from published segment`

O sintoma indica que a recuperação dos segmentos publicados não está tratando
corretamente a equivalência entre manifesto persistido e artefato físico no disco
durante restart/replay.

## Abordagem escolhida

### Opção 1 — correção focal por causa raiz

Corrigir cada grupo de falhas sem ampliar o escopo estrutural do módulo além do
necessário para restaurar o contrato.

**Motivos da escolha**

- Minimiza risco em módulos já sensíveis de restart e causalidade.
- Mantém alta rastreabilidade entre falha, correção, teste e documento.
- Facilita comparar o resultado final com `PHASE-1-VALIDATION.md`,
  `PHASE-2-VALIDATION.md` e os runbooks existentes.

## Estratégia de execução

1. Reproduzir falhas em grupos menores.
2. Investigar causa raiz antes de alterar produção.
3. Escrever/usar testes focados como proteção de regressão.
4. Implementar a menor mudança possível para restaurar o contrato.
5. Rodar regressão do grupo corrigido.
6. Repetir para o segundo grupo.
7. Rodar suíte focal do NexusTrade.
8. Rodar verificações complementares (`compileall`, `pip check`, `docker compose config --quiet`).
9. Subir stack local e validar em navegador.
10. Atualizar documentação e checklist de VPS com base no estado final.

## Critérios de aceite deste ciclo

- Todos os testes hoje falhando em `learning_lab` e `tick_archive` ficam verdes.
- A suíte focal do NexusTrade volta a ficar verde no ambiente local com `.env`.
- O launcher local continua seguro em DEMO.
- O frontend do NexusTrade abre e reconcilia sem erros visíveis no navegador.
- A documentação de validação/runbook deixa de afirmar algo que o código atual não
  consiga reproduzir localmente.

## Fora de escopo neste ciclo

- Refatoração ampla de `learning_lab.py` ou `tick_archive.py` sem relação direta com a
  causa raiz.
- Mudanças de estratégia do Champion V1.
- Mudanças de UX além do necessário para validar o fluxo final.
- Deploy efetivo na VPS.

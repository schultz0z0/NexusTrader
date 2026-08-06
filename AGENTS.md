# NexusTrader — instruções para agentes

Antes de analisar ou alterar o projeto, leia `docs/CURRENT-STATE.md`. Ele é o ponto de
entrada canônico para o estado vigente. Use `docs/AUDIT-2026-08-06.md` como evidência
detalhada e os demais documentos por assunto.

## Restrições do produto

- Não altere o conceito, regras ou parâmetros de `Donchian+ZigZag` sem autorização
  explícita do proprietário.
- O único perfil habilitado atualmente é `donchian`, `R_75`, candles de 60 segundos e
  contratos de 2 minutos. Não reintroduza Bollinger nem defaults antigos.
- Preserve a arquitetura multi-robô, embora somente Donchian+ZigZag esteja habilitado.
- Valide operações primeiro em conta Deriv DEMO. O launcher local bloqueia conta REAL.
- Não publique, faça deploy na VPS ou envie ordem REAL sem autorização explícita.
- Nunca imprima ou versione PATs, chaves, OTPs, tickets WebSocket ou tokens de
  notificação. O token Telegram exposto no histórico Git ainda precisa ser rotacionado.

## Leitura e validação

- Trate `docs/superpowers/`, `docs/plans/` e auditorias anteriores como histórico, não
  como especificação atual.
- Para desenvolvimento local, use `scripts/start_dev.ps1` e o banco isolado em
  `storage/nexus_trader.dev.db`.
- Antes de concluir mudanças, execute os testes pertinentes, `git diff --check` e
  confirme que `strategies/donchian_zigzag.py` e `utils/indicators.py` não mudaram se a
  tarefa não autorizou alteração da estratégia.

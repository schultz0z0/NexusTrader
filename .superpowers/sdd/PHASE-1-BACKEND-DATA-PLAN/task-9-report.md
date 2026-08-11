# Task 9 — promoção humana, reanálise, rotação Trial e rollback

Data: 2026-08-10
Branch: `feature/nexustrade-learning`
Escopo: transições governadas e transacionais do NexusTrade. Nenhuma promoção automática, operação REAL ou integração Deriv foi adicionada.

## RED → GREEN

- Aprovação começou com `ModuleNotFoundError: nexus_trade.promotion`; o RED seguinte demonstrou que uma proposta ainda inválida era aceita antes das validações completas.
- Injeção de falha começou com construtor sem suporte ao injetor; o GREEN provou rollback integral após a troca local de pointer, sem evento falso de sucesso.
- Reanálise, rotação Trial e rollback começaram com `AttributeError` para os métodos ainda ausentes e ganharam REDs incrementais antes de cada implementação.
- A API começou em `404`; as rotas finas passaram a exigir a autenticação já aplicada ao router e delegar ao serviço transacional.
- O replay de `request_id` inicialmente colidia com a revisão já consumida; o GREEN tornou o replay idêntico idempotente e o reuso com entrada diferente um conflito auditado.
- Um relatório `EVOLVE` com manifesto incompleto chegou a ser aceito; o último RED passou a exigir o conjunto exato de 19 gates e seus campos governados.

## Garantias implementadas

- `approve` exige ator humano autenticado, motivo, `request_id`, CAS de `config_revision`, Champion `OFF` e revalida dentro de `BEGIN IMMEDIATE` que a lane Champion não possui estado reservado/ativo/quarentenado, intent não resolvido ou contrato aberto.
- A aprovação valida proposta pendente, campanha e Trial correntes, relatório semanal alinhado, sete dias, 300 decisões, recomendação, gates duros, hashes canônicos e identidade exata de relatório, candidato, artifact, config, dataset, provenance e versão. O Champion nunca é promovido automaticamente.
- A transação cria nova versão/snapshot Champion imutável e troca o pointer atomicamente; a versão anterior e todo o histórico permanecem preservados. Auditoria e outbox são gravados na mesma transação e eventos só são entregues depois do commit.
- `reanalyze` nunca altera Champion ou evidência congelada: fecha proposta/campanha e inicia a campanha Trial pretendida em `0/300`, preservando histórico e aprendizagem.
- A rotação Trial ocorre somente no instante exato de segunda-feira, 10:00, `America/Sao_Paulo`. Exige candidato SHADOW qualificado e ausência de proposta pendente; caso contrário preserva o progresso. A troca A→B é atômica, idempotente e segura sob concorrência, marca A como `SUPERSEDED`, inicia B em zero e nunca toca Champion/REAL.
- `rollback` é explícito e humano, usa a mesma barreira de segurança, CAS, `request_id` e validação de hash/snapshot. Reaponta para uma versão Champion preservada, sem apagar a versão degradada, e sobrevive a restart.
- Toda tentativa coberta — commit, replay, rejeição, conflito e falha injetada — deixa auditoria sanitizada com ator, motivo, request, revisões, antes/depois, hashes, resultado e erro, sem secrets.
- Outbox e IDs de evento são determinísticos. As rotas publicam somente após commit e retornam snapshot durável de reparo consistente.
- Migração aditiva atualiza banco legado sem perder auditoria e é segura para repetição.

## Evidência final

- Focado promoção/governança: `python -m unittest tests.test_nexus_trade_promotion -v` — **21/21**, 3,726 s.
- Integração Task 6–9 (promoção, API, relatórios, gates, learning, runtime e repository) — **123/123**, 18,305 s; o teste adicional do manifesto exato também está incluído no full final.
- Regressões protegidas (`donchian_profile`, `nexus_speed`, `nexus_speed_runtime`, `control_plane`, `repository`) — **78/78**, 3,857 s.
- Full Python: `python -m unittest discover -s tests -v` — **419/419**, 38,216 s.
- JavaScript: `node --test tests/js/*.test.mjs` — **17/17**.
- `python -m compileall -q api backtest core data database nexus_trade risk strategies trading` — exit 0.
- `python -m pip check` — `No broken requirements found.`
- `git diff --check` — exit 0.
- Diff dos arquivos protegidos `strategies/donchian_zigzag.py`, `utils/indicators.py` e `strategies/nexus_speed.py` — vazio.
- Único aviso: `StarletteDeprecationWarning` preexistente do FastAPI TestClient/httpx.
- Nenhum acesso Deriv, ordem REAL, Task 10, push ou deploy foi realizado.

## Fix round 1/5 — autenticação e invariantes de transição

Os seis achados da revisão foram reproduzidos por REDs comportamentais antes das correções:

- a chave compartilhada do dashboard e um `actor` forjado no JSON aprovavam uma promoção (`200` em vez de `403`);
- o parser da lane lia `state` como string e ignorava o formato durável real `state.position_status`, inclusive `RESERVED`, além de aceitar estado ausente/malformado;
- a rotação semanal trocava versão/campanha, mas deixava A como `TRIAL` e B como `SHADOW`;
- uma recomendação `REANALYZE` com todos os gates `PASS` não exigia confirmação reforçada;
- payload corrompido em reanálise era revertido sem auditoria da tentativa;
- replay de outbox com request `replay_%` capturava evento estrangeiro por semântica wildcard de SQL `LIKE` (quatro eventos em vez de três).

As três rotas humanas agora exigem, além da chave do dashboard, `NEXUS_HUMAN_ACTION_KEY`, exclusiva e comparada em tempo constante. A identidade auditada vem apenas de `NEXUS_HUMAN_ACTOR`; `actor` do corpo não possui autoridade. Configuração ausente falha fechada, a chave não entra em resposta/snapshot/audit, e a rotação automática segue restrita ao serviço `system:scheduler`. Compose e documentação declaram as novas variáveis sem incluir secrets reais.

O safety check interpreta estritamente o payload durável, aceita como seguro somente `IDLE` conhecido sem owner/decision/contract/quarantine e rejeita todos os estados governados como inseguros. A rotação grava uma autorização append-only que vincula boundary, request, ator, motivo, candidatos A/B, versões e campanhas; triggers permitem somente A `TRIAL→SHADOW` e B `SHADOW→TRIAL` associados a essa autorização válida. Versão, campanhas, papéis, pointer, CAS, audit e outbox permanecem na mesma transação, com rollback comprovado após failure injection e persistência/revalidação em restart.

`REANALYZE` sempre exige confirmação reforçada, independentemente dos demais gates. Propostas corruptas de reanálise geram auditoria `REJECTED/PROPOSAL_CORRUPT` antes da exceção, sem mudar revision, pointers ou campanhas. A outbox recebeu colunas/index explícitos de `action` e `request_id`; leitura usa igualdade parametrizada e a migração preenche legado somente após validar a identidade contra requests/boundaries duráveis.

### Evidência do fix

- Focado Task 9: **30/30**, 5,335 s.
- Integração Task 6–9, settings e deploy: **147/147**, 21,027 s.
- Regressões protegidas: **78/78**, 3,783 s.
- Full Python: **428/428**, 42,830 s.
- JavaScript: **17/17**.
- `compileall`, `pip check`, `docker compose config --quiet`, `git diff --check` e diff dos três robôs/indicadores protegidos: verdes/limpos.
- O compose foi validado com `.env.example` e valores dummy em memória; nenhum arquivo de segredo foi criado. Permanece apenas o `StarletteDeprecationWarning` preexistente.
- Nenhum acesso Deriv, ordem REAL, Task 10, push ou deploy foi realizado.

## Fix round 2/5 — credencial humana exclusiva

O achado foi reproduzido antes da correção: configurações em que `NEXUS_HUMAN_ACTION_KEY` coincidia com `DASHBOARD_API_KEY`, `INTERNAL_API_TOKEN` ou `DERIV_API_TOKEN` eram aceitas nas três subcases do RED, anulando a separação de autoridade.

O validator de `Settings` agora normaliza valores não vazios e usa `secrets.compare_digest` para rejeitar cada colisão, tanto em produção quanto em `DEV_MODE` quando a credencial humana está configurada. Valores distintos continuam válidos; DEV ainda permite credenciais humanas vazias e produção continua exigindo chave e ator. A mensagem é genérica e `hide_input_in_errors` impede que o Pydantic inclua inputs secretos no erro.

### Evidência do fix

- Focados settings/NexusTrade promotion: **45/45**, 5,955 s.
- Integração Task 6–9, settings e deploy: **151/151**, 21,187 s.
- Regressões protegidas: **78/78**, 4,248 s.
- Full Python: **432/432**, 47,292 s.
- JavaScript: **17/17**.
- `compileall`, `pip check`, `docker compose config --quiet`, `git diff --check` e diff dos arquivos protegidos: verdes/limpos.
- Nenhum secret foi registrado; nenhum acesso Deriv, ordem REAL, Task 10, push ou deploy foi realizado.

## Fix round 3/5 — redação estrutural de erros de settings

O RED serializou as exceções de colisão e de campos obrigatórios por `str`, `repr`, `errors()` e `json()`. Embora a forma textual estivesse ocultada, `ValidationError.errors()` ainda continha o dicionário completo de input com credenciais sentinel; foram dez subfalhas exatas entre colisões em produção/DEV e quatro authorities obrigatórias ausentes.

As regras pós-carregamento agora executam depois de `BaseSettings.__init__` e levantam `SettingsConfigurationError`, uma subclasse simples de `ValueError` que não carrega o input estruturado do Pydantic. Parsing de campos, `.env`, variáveis de ambiente e overrides continuam sob `BaseSettings`; required/collisions/DEV e todos os limites anteriores permanecem iguais. A remoção do `model_validator` também impede que outros checks pós-load recriem a mesma superfície de vazamento.

### Evidência do fix

- RED: **2 métodos / 10 subfalhas**, com sentinels visíveis em `errors()`.
- Focados settings/NexusTrade promotion: **45/45**, 5,955 s.
- Integração Task 6–9, settings e deploy: **151/151**, 20,432 s.
- Regressões protegidas: **78/78**, 4,001 s.
- Full Python: **432/432**, 43,483 s.
- JavaScript: **17/17**.
- `compileall`, `pip check`, `docker compose config --quiet`, `git diff --check` e diff dos arquivos protegidos: verdes/limpos.
- Nenhum secret foi logado; nenhum acesso Deriv, ordem REAL, Task 10, push ou deploy foi realizado.

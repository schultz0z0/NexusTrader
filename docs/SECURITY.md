# Segurança

## Controles implementados

- Produção (`DOMAIN` preenchido) não inicia sem dois segredos independentes.
- Dashboard REST usa `X-API-Key`; WebSocket é autenticado e isolado por `bot_id`.
- Comunicação bot → API usa `X-Internal-Token`.
- CORS não aceita origens arbitrárias.
- Conta real é bloqueada por padrão e só é aceita quando `ALLOW_REAL_TRADING=true`; conta/tipo são revalidados na Deriv no start e a flag é conferida novamente imediatamente antes de `buy`.
- O dashboard destaca conta real em vermelho e exige confirmação adicional antes do comando de início.
- Containers usam `no-new-privileges` e a porta local escuta em loopback por padrão.
- Tokens não aparecem em exemplos de documentação.

## Segredos

O `.env` não deve ser versionado. Proteja permissões na VPS, evite imprimir PAT/OTP e gere chaves com fonte criptograficamente segura. A URL OTP é credencial temporária: logs exibem no máximo um prefixo, nunca a URL completa.

O token informado anteriormente pode continuar funcional conforme decisão do proprietário; sua presença passada em documentação ou histórico Git ainda deve ser considerada exposição. Rotacionar futuramente continua recomendado, mesmo que a conta real esteja sem saldo.

## Limites atuais

- A chave do dashboard fica no armazenamento local do navegador para reconectar o WebSocket. Use apenas dispositivo confiável e HTTPS.
- SQLite e HTTP interno são adequados a uma única VPS; escala horizontal exige banco/filas compartilhados e leasing distribuído.
- Não há autenticação multiusuário, RBAC ou trilha de auditoria administrativa nesta versão.
- A biblioteca de gráficos vem de CDN; produção com exigência de supply-chain estrita deve vendorizá-la e adicionar CSP/SRI.

## Recomendações da VPS

Mantenha Docker/host atualizados, firewall liberando apenas SSH e HTTPS, SSH por chave, backups criptografados, proxy com TLS e rate limit nas rotas de controle. Nunca exponha diretamente a porta `8989` na internet.

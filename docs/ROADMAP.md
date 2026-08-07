# Roadmap pós-prontidão

A fase de correção funcional, ownership, risco persistente, replay, readiness, seletor
REAL protegido e hardening Docker foi concluída no código. Próximas evoluções não são
pré-requisitos para o deploy de uma única VPS:

1. campanha DEMO longa e datasets out-of-sample versionados para avaliar performance;
2. métricas Prometheus, alertas externos e tracing distribuído;
3. autenticação multiusuário, RBAC e trilha administrativa;
4. PostgreSQL e fila durável para escala horizontal com leases distribuídos;
5. orçamento agregado e scheduler por conta;
6. venda da biblioteca de gráfico no próprio bundle com SRI/build frontend.

Conta REAL nunca é liberada automaticamente por métrica. A decisão exige aceite humano,
teto de stake definido e compreensão de que performance passada não garante resultado.

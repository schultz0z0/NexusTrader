# Security Scan Report

**Repository:** `C:\Users\raphaeloliveira\Desktop\Projetos Saas\Nova pasta (2)`  
**Scan ID:** `6fd8dce1-389f-4791-be3f-f40b4eb9d9d0`  
**Started:** 2026-08-06T20:04:39.545102+00:00  
**Sealed:** 2026-08-06T21:03:07.325023+00:00

## Executive Summary

This scan identified **4 finding(s)** across the repository:

- 🟡 **Medium:** 4

## Findings Overview

| # | Severity | Category | File | Status | Title |
|---|----------|----------|------|--------|-------|
| 1 | medium | CWE-1021 | `api/app.py:63` | fixed | Dashboard suscetível a clickjacking |
| 2 | medium | CWE-306 | `api/auth.py:6` | fixed | Autenticação fail-open fora do Compose |
| 3 | medium | CWE-798 | `deploy/DEPLOY.md:43` | open | Token Telegram ativo exposto no histórico Git |
| 4 | medium | CWE-598 | `static/js/api.js:34` | fixed | Chave de controle exposta na URL WebSocket |

## Detailed Findings

### 1. Dashboard suscetível a clickjacking

- **Severity:** medium
- **Category:** CWE-1021
- **Status:** fixed
- **Location:** `api/app.py:63`

Respostas do dashboard não restringiam framing, permitindo sobreposição de controles autenticados em site malicioso.

**Evidence:**

```
Produção respondeu sem X-Frame-Options e sem CSP frame-ancestors; corrigido por middleware com DENY/frame-ancestors none.
```

### 2. Autenticação fail-open fora do Compose

- **Severity:** medium
- **Category:** CWE-306
- **Status:** fixed
- **Location:** `api/auth.py:6`

Execução direta com segredos vazios aceitava requisições e WebSockets sem credencial.

**Evidence:**

```
As funções de autenticação só comparavam quando expected era não vazio; corrigido com DEV_MODE explícito e validação fail-closed.
```

### 3. Token Telegram ativo exposto no histórico Git

- **Severity:** medium
- **Category:** CWE-798
- **Status:** open
- **Location:** `deploy/DEPLOY.md:43`

O token atual do bot Telegram coincide com credencial publicada em commit histórico; qualquer leitor do repositório pode controlar o canal do bot até a rotação.

**Evidence:**

```
Correspondência confirmada sem registrar o valor: commit 43789a1, deploy/DEPLOY.md:43; uso em notifications/telegram_notifier.py e telegram_listener.py.
```

### 4. Chave de controle exposta na URL WebSocket

- **Severity:** medium
- **Category:** CWE-598
- **Status:** fixed
- **Location:** `static/js/api.js:34`

O dashboard colocava a chave de controle na query do WebSocket, que era registrada integralmente pelo access log do Uvicorn.

**Evidence:**

```
Fluxo confirmado entre static/js/api.js e api/app.py; log local reproduziu query. Corrigido com ticket aleatório, curto e single-use.
```

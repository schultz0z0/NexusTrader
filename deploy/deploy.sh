#!/bin/bash

# ==============================================================================
# Script de Deploy Automatizado para NexusTrader na VPS Ubuntu (com Traefik)
# ==============================================================================

set -e

echo "🚀 Iniciando deploy do NexusTrader na VPS..."

# 1. Atualiza codigo fonte via Git se for um repositorio
if [ -d ".git" ]; then
    echo "📥 Atualizando repositorio Git..."
    git pull origin main || git pull origin master
fi

# 2. Verifica se o arquivo .env existe
if [ ! -f ".env" ]; then
    echo "⚠️ Arquivo .env nao encontrado! Criando a partir de .env.example..."
    cp .env.example .env
    echo "❗ Por favor, edite o arquivo .env com seu DOMAIN e credenciais antes de continuar."
    exit 1
fi

# 3. Constroi e reinicia os containers via Docker Compose
echo "🐳 Construindo imagens e subindo os containers com Docker Compose..."
docker compose build --no-cache
docker compose down || true
docker compose up -d

echo "✨ Deploy concluido com sucesso!"
echo "📊 Verificando status dos containers:"
docker compose ps

echo "📜 Para acompanhar os logs em tempo real:"
echo "   docker compose logs -f"

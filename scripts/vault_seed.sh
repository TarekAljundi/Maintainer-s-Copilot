#!/bin/sh
# Seed Vault KV v2 layout for Maintainer's Copilot. Idempotent.
# Required env: VAULT_ADDR, VAULT_TOKEN. Optional: GROQ_API_KEY.
# See PRD §Vault secret organization (Q30).
set -eu

vault secrets enable -path=secret -version=2 kv 2>/dev/null || true

vault kv put secret/shared/jwt \
  signing_key="dev-jwt-signing-key-please-rotate-in-prod"

vault kv put secret/api/llm \
  groq_api_key="${GROQ_API_KEY:-placeholder}" \
  openrouter_api_key="${OPENROUTER_API_KEY:-placeholder}"

vault kv put secret/api/tracing \
  langfuse_public_key="${LANGFUSE_PUBLIC_KEY:-placeholder}" \
  langfuse_secret_key="${LANGFUSE_SECRET_KEY:-placeholder}" \
  langfuse_host="${LANGFUSE_HOST:-http://langfuse:3000}"

vault kv put secret/api/db \
  url="postgresql+asyncpg://${POSTGRES_USER:-copilot}@db:5432/${POSTGRES_DB:-copilot}"

vault kv put secret/api/redis \
  url="redis://redis:6379/0"

vault kv put secret/api/blob \
  endpoint="minio:9000" \
  access_key="minioadmin" \
  secret_key="minioadmin" \
  bucket="mc-evals"

vault kv put secret/streamlit/api \
  api_base="http://api:8000"

echo "vault seed complete"

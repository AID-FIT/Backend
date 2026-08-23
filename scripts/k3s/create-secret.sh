#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-aidfit}"
SECRET_NAME="${SECRET_NAME:-aidfit-backend-secret}"

required_vars=(
  POSTGRES_USER
  POSTGRES_PASSWORD
  POSTGRES_DB
  JWT_SECRET_KEY
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required env: ${var_name}" >&2
    exit 1
  fi
done

DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres-svc:5432/${POSTGRES_DB}}"

kubectl create secret generic "${SECRET_NAME}" \
  --namespace="${NAMESPACE}" \
  --from-literal=APP_NAME="${APP_NAME:-AID-FIT Backend}" \
  --from-literal=ENVIRONMENT="${ENVIRONMENT:-production}" \
  --from-literal=API_V1_PREFIX="${API_V1_PREFIX:-/api/v1}" \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --from-literal=POSTGRES_USER="${POSTGRES_USER}" \
  --from-literal=POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=POSTGRES_DB="${POSTGRES_DB}" \
  --from-literal=JWT_SECRET_KEY="${JWT_SECRET_KEY}" \
  --from-literal=JWT_ALGORITHM="${JWT_ALGORITHM:-HS256}" \
  --from-literal=ACCESS_TOKEN_EXPIRE_MINUTES="${ACCESS_TOKEN_EXPIRE_MINUTES:-10080}" \
  --from-literal=LOCAL_UPLOAD_DIR="${LOCAL_UPLOAD_DIR:-uploads}" \
  --from-literal=PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://localhost:12570}" \
  --from-literal=CORS_ORIGINS="${CORS_ORIGINS:-*}" \
  --from-literal=USE_MOCK_AI="${USE_MOCK_AI:-true}" \
  --from-literal=RAG_VECTOR_DB_PATH="${RAG_VECTOR_DB_PATH:-/app/data/chromadb_final}" \
  --from-literal=RAG_COLLECTION_NAME="${RAG_COLLECTION_NAME:-musinsa}" \
  --from-literal=RAG_EMBEDDING_MODEL="${RAG_EMBEDDING_MODEL:-jhgan/ko-sroberta-multitask}" \
  --from-literal=RAG_EMBEDDING_CACHE_PATH="${RAG_EMBEDDING_CACHE_PATH:-/app/data/huggingface}" \
  --from-literal=RAG_EMBEDDING_LOCAL_FILES_ONLY="${RAG_EMBEDDING_LOCAL_FILES_ONLY:-false}" \
  --from-literal=GOOGLE_CLIENT_IDS="${GOOGLE_CLIENT_IDS:-}" \
  --from-literal=APPLE_CLIENT_IDS="${APPLE_CLIENT_IDS:-}" \
  --from-literal=AUTH_ALLOW_UNVERIFIED_TOKENS="${AUTH_ALLOW_UNVERIFIED_TOKENS:-false}" \
  --dry-run=client -o yaml | kubectl apply -f -


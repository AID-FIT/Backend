#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAMESPACE="${NAMESPACE:-aidfit}"
APP_LABEL="${APP_LABEL:-aidfit-backend}"
CONTAINER_NAME="${CONTAINER_NAME:-aidfit-backend}"
SOURCE_DIR="${RAG_DATA_DIR:-${ROOT_DIR}/data}"

if [[ ! -f "${SOURCE_DIR}/chromadb_final/chroma.sqlite3" ]]; then
  echo "Missing ChromaDB: ${SOURCE_DIR}/chromadb_final/chroma.sqlite3" >&2
  exit 1
fi

pod_name="$(
  kubectl get pods \
    --namespace="${NAMESPACE}" \
    --selector="app=${APP_LABEL}" \
    --field-selector=status.phase=Running \
    --output=jsonpath='{.items[0].metadata.name}'
)"

if [[ -z "${pod_name}" ]]; then
  echo "No running backend pod found for app=${APP_LABEL}" >&2
  exit 1
fi

if kubectl exec \
  --namespace="${NAMESPACE}" \
  "${pod_name}" \
  --container="${CONTAINER_NAME}" \
  -- test -f /app/data/chromadb_final/chroma.sqlite3; then
  echo "ChromaDB already exists in backend-rag-pvc; refusing to overwrite it." >&2
  exit 1
fi

for directory in chromadb_final huggingface; do
  if [[ ! -d "${SOURCE_DIR}/${directory}" ]]; then
    continue
  fi
  kubectl exec \
    --namespace="${NAMESPACE}" \
    "${pod_name}" \
    --container="${CONTAINER_NAME}" \
    -- mkdir -p "/app/data/${directory}"
  kubectl cp \
    "${SOURCE_DIR}/${directory}/." \
    "${NAMESPACE}/${pod_name}:/app/data/${directory}" \
    --container="${CONTAINER_NAME}"
done

kubectl exec \
  --namespace="${NAMESPACE}" \
  "${pod_name}" \
  --container="${CONTAINER_NAME}" \
  -- python -c 'import sqlite3; connection = sqlite3.connect("/app/data/chromadb_final/chroma.sqlite3"); print(connection.execute("SELECT name, dimension FROM collections").fetchall()); print("embeddings", connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]); connection.close()'

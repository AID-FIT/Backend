#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAMESPACE="${NAMESPACE:-aidfit}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-aidfit-backend}"

kubectl apply -f "${ROOT_DIR}/k8s/namespace.yaml"

if [[ -f "${ROOT_DIR}/.env.k3s" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env.k3s"
  set +a
fi

"${ROOT_DIR}/scripts/k3s/create-secret.sh"

kubectl apply -f "${ROOT_DIR}/k8s/postgres-pvc.yaml"
kubectl apply -f "${ROOT_DIR}/k8s/postgres-deployment.yaml"
kubectl apply -f "${ROOT_DIR}/k8s/postgres-service.yaml"

kubectl rollout status deployment/postgres -n "${NAMESPACE}" --timeout=2m

kubectl apply -f "${ROOT_DIR}/k8s/backend-service.yaml"
kubectl apply -f "${ROOT_DIR}/k8s/backend-deployment.yaml"

kubectl rollout status deployment/"${DEPLOYMENT_NAME}" -n "${NAMESPACE}" --timeout=3m
kubectl get pods -n "${NAMESPACE}" -o wide
kubectl get services -n "${NAMESPACE}"


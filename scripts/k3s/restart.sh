#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-aidfit}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-aidfit-backend}"

kubectl rollout restart deployment/"${DEPLOYMENT_NAME}" -n "${NAMESPACE}"
kubectl rollout status deployment/"${DEPLOYMENT_NAME}" -n "${NAMESPACE}" --timeout=3m


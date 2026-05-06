#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-aidfit}"

kubectl delete -f k8s/backend-deployment.yaml --ignore-not-found
kubectl delete -f k8s/backend-service.yaml --ignore-not-found
kubectl delete -f k8s/postgres-deployment.yaml --ignore-not-found
kubectl delete -f k8s/postgres-service.yaml --ignore-not-found

echo "PVC and namespace were preserved. Delete manually only if you intentionally want to remove data:"
echo "  kubectl delete pvc postgres-pvc -n ${NAMESPACE}"
echo "  kubectl delete namespace ${NAMESPACE}"


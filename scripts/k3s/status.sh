#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-aidfit}"

kubectl get deployments -n "${NAMESPACE}"
kubectl get pods -n "${NAMESPACE}" -o wide
kubectl get services -n "${NAMESPACE}"
kubectl get pvc -n "${NAMESPACE}"


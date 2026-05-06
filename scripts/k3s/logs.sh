#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-aidfit}"
APP_LABEL="${APP_LABEL:-aidfit-backend}"
CONTAINER="${CONTAINER:-aidfit-backend}"

kubectl logs -n "${NAMESPACE}" -l app="${APP_LABEL}" -c "${CONTAINER}" --tail="${TAIL:-200}" -f


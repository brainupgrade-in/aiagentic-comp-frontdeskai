#!/bin/bash
# Deploy FrontDesk AI into a participant namespace on the Spark cluster.
#
# Run it from your JupyterLab terminal — APP_NAMESPACE and APP_HOST are already
# in your environment, so this needs no arguments:
#
#   bash scripts/deploy-spark.sh
#
# Overrides:
#   IMAGE=brainupgrade/frontdeskai:<tag>   pin a specific build
#   AUTH_PASSWORD=...                      first-login password (default brainupgrade)
#
# This is the Spark path. scripts/deploy.sh is the kind/local path and is
# unchanged; the two use different manifests because the participant namespace
# forbids NodePorts and caps memory at 1Gi.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MANIFESTS="${REPO_DIR}/scripts/manifests/spark"

NAMESPACE="${APP_NAMESPACE:-$(kubectl config view --minify -o jsonpath='{..namespace}' 2>/dev/null || true)}"
if [ -z "${NAMESPACE}" ]; then
  echo "ERROR: could not determine the namespace."
  echo "       Set APP_NAMESPACE, e.g. APP_NAMESPACE=agenticaiu31 bash $0"
  exit 1
fi

if [ -z "${APP_HOST:-}" ]; then
  echo "ERROR: APP_HOST is not set — no hostname to serve the app on."
  echo "       It is normally injected into your sandbox. Set it by hand if not:"
  echo "       APP_HOST=<your -app hostname> bash $0"
  exit 1
fi

IMAGE="${IMAGE:-brainupgrade/frontdeskai:latest}"
LLM_SECRET="${LLM_SECRET:-${NAMESPACE}-llm}"
AUTH_PASSWORD="${AUTH_PASSWORD:-brainupgrade}"

echo "==> Namespace: ${NAMESPACE}"
echo "==> Host:      https://${APP_HOST}"
echo "==> Image:     ${IMAGE}"

if ! kubectl -n "${NAMESPACE}" get secret "${LLM_SECRET}" >/dev/null 2>&1; then
  echo "WARNING: Secret '${LLM_SECRET}' not found in ${NAMESPACE}."
  echo "         The app will start but has no LLM gateway credential."
fi

# ── Secret: SECRET_KEY must survive a redeploy ───────────────────────────────
# It is the Fernet key for encrypted per-skill config in the database. A new
# key on every deploy makes previously stored skill credentials unreadable.
EXISTING_KEY=$(kubectl -n "${NAMESPACE}" get secret frontdeskai-secret \
                 -o jsonpath='{.data.SECRET_KEY}' 2>/dev/null | base64 -d 2>/dev/null || true)
if [ -n "${EXISTING_KEY}" ]; then
  echo "==> Reusing the existing SECRET_KEY"
  SECRET_KEY="${EXISTING_KEY}"
else
  echo "==> Generating a SECRET_KEY"
  SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
fi

kubectl -n "${NAMESPACE}" create secret generic frontdeskai-secret \
  --from-literal=SECRET_KEY="${SECRET_KEY}" \
  --from-literal=AUTH_PASSWORD="${AUTH_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# ── Manifests ────────────────────────────────────────────────────────────────
echo "==> Applying manifests"
kubectl -n "${NAMESPACE}" apply -f "${MANIFESTS}/configmap.yaml"
kubectl -n "${NAMESPACE}" apply -f "${MANIFESTS}/service.yaml"

sed -e "s|image: DOCKERHUB_USERNAME/frontdeskai:latest|image: ${IMAGE}|" \
    -e "s|name: LLM_SECRET_NAME|name: ${LLM_SECRET}|" \
    "${MANIFESTS}/deployment.yaml" | kubectl -n "${NAMESPACE}" apply -f -

sed -e "s|host: APP_HOST|host: ${APP_HOST}|" \
    "${MANIFESTS}/ingress.yaml" | kubectl -n "${NAMESPACE}" apply -f -

# Picks up a changed ConfigMap, which a pod does not reload on its own.
kubectl -n "${NAMESPACE}" rollout restart deployment/frontdeskai
echo "==> Waiting for the app to be ready (first pull is slow)..."
kubectl -n "${NAMESPACE}" rollout status deployment/frontdeskai --timeout=300s

echo ""
echo "==> FrontDesk AI deployed."
echo "    URL:    https://${APP_HOST}"
echo "    Login:  rajesh.kumar@unigps.in / ${AUTH_PASSWORD}"
echo "    Logs:   kubectl -n ${NAMESPACE} logs -f deploy/frontdeskai"

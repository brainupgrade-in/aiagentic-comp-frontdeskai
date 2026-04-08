#!/bin/bash
# Deploy FrontDesk AI.
# Auto-detects environment:
#   - kind context  → local image load, port-forward for access
#   - other context → production k3s sandbox (brainupgrade.in registry + ingress)
#
# Usage: bash k8s/deploy.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${REPO_DIR}/.env"
CLUSTER_NAME="frontdeskai"
LOCAL_IMAGE="frontdeskai:latest"

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: .env file not found at ${ENV_FILE}"
  echo "       Copy .env.example to .env and fill in your API keys."
  exit 1
fi

# Source .env file (skip comments and blank lines)
while IFS='=' read -r key value; do
  key="${key%%#*}"
  key="${key// /}"
  [[ -z "$key" ]] && continue
  [[ "$key" =~ ^# ]] && continue
  value="${value%%#*}"
  value="${value#\"}"
  value="${value%\"}"
  value="${value%"${value##*[![:space:]]}"}"
  export "$key=$value"
done < "${ENV_FILE}"

GROQ_API_KEY="${GROQ_API_KEY:?ERROR: GROQ_API_KEY not set in .env file}"
AUTH_PASSWORD="${AUTH_PASSWORD:?ERROR: AUTH_PASSWORD not set in .env file}"

CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")

# ── Helpers ──────────────────────────────────────────────────────────────────
apply_secret() {
  local image="$1"
  echo "==> Creating secret..."
  SECRET_ARGS=(
    --from-literal=GROQ_API_KEY="${GROQ_API_KEY}"
    --from-literal=SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
    --from-literal=AUTH_PASSWORD="${AUTH_PASSWORD}"
  )
  if [ -n "${LANGFUSE_SECRET_KEY:-}" ] && [ -n "${LANGFUSE_PUBLIC_KEY:-}" ] && [ -n "${LANGFUSE_HOST:-}" ]; then
    SECRET_ARGS+=(
      --from-literal=LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY}"
      --from-literal=LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY}"
      --from-literal=LANGFUSE_HOST="${LANGFUSE_HOST}"
    )
    echo "    Langfuse keys included"
  else
    echo "    Langfuse keys not set, skipping"
  fi
  kubectl create secret generic frontdeskai-secret \
    "${SECRET_ARGS[@]}" \
    --dry-run=client -o yaml | kubectl apply -f -
}

apply_manifests() {
  local image="$1"
  local pull_policy="$2"
  echo "==> Applying manifests (image=${image}, pullPolicy=${pull_policy})..."
  DEPLOY_TMP=$(mktemp)
  trap 'rm -f "${DEPLOY_TMP}"' EXIT
  sed "s|image: frontdeskai:latest|image: ${image}|g" \
      "${REPO_DIR}/k8s/deployment.yaml" \
    | sed "s|imagePullPolicy: Never|imagePullPolicy: ${pull_policy}|g" \
    > "${DEPLOY_TMP}"
  kubectl apply -f "${DEPLOY_TMP}"
  kubectl apply -f "${REPO_DIR}/k8s/service.yaml"
  if kubectl apply -f "${REPO_DIR}/k8s/servicemonitor.yaml" 2>/dev/null; then
    echo "==> ServiceMonitor deployed"
  else
    echo "==> ServiceMonitor skipped (CRD not found or insufficient permissions)"
  fi
}

# ── kind (devcontainer / Codespace) ──────────────────────────────────────────
if echo "${CURRENT_CONTEXT}" | grep -q "kind"; then
  echo "==> kind cluster detected: ${CURRENT_CONTEXT}"

  echo "==> Building image: ${LOCAL_IMAGE}"
  docker build -t "${LOCAL_IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"

  echo "==> Loading image into kind cluster '${CLUSTER_NAME}'..."
  kind load docker-image "${LOCAL_IMAGE}" --name "${CLUSTER_NAME}"

  apply_secret "${LOCAL_IMAGE}"
  apply_manifests "${LOCAL_IMAGE}" "Never"

  echo "==> Waiting for app to be ready..."
  kubectl wait --for=condition=ready pod -l app=frontdeskai --timeout=120s

  echo ""
  echo "==> FrontDesk AI deployed to kind!"
  echo "    Access the app:"
  echo "      kubectl port-forward svc/frontdeskai 8000:80 &"
  echo "      open http://localhost:8000"
  echo "    Prometheus metrics:"
  echo "      kubectl port-forward svc/frontdeskai 9090:9090 &"
  echo "    Verify: kubectl get pods -l app=frontdeskai"

# ── Production k3s sandbox (brainupgrade.in) ─────────────────────────────────
else
  NAMESPACE=$(kubectl config get-contexts "${CURRENT_CONTEXT}" --no-headers | awk '{print $5}')
  if [ -z "${NAMESPACE}" ]; then
    echo "ERROR: Could not detect namespace from kubectl context."
    exit 1
  fi
  REGISTRY="${NAMESPACE}-registry.brainupgrade.in"
  IMAGE="${REGISTRY}/frontdeskai:latest"
  echo "==> Namespace: ${NAMESPACE}"
  echo "==> Registry:  ${REGISTRY}"

  REGISTRY_TMP=$(mktemp)
  trap 'rm -f "${REGISTRY_TMP}"' EXIT
  sed "s/YOURNAMESPACE/${NAMESPACE}/g" "${REPO_DIR}/k8s/registry.yaml" > "${REGISTRY_TMP}"

  echo "==> Deploying private registry..."
  kubectl apply -f "${REGISTRY_TMP}"
  kubectl wait --for=condition=ready pod -l app=registry --timeout=120s

  echo "==> Building and pushing image..."
  docker build -t "${IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"
  docker push "${IMAGE}"

  apply_secret "${IMAGE}"
  apply_manifests "${IMAGE}" "Always"

  echo "==> Waiting for app to be ready..."
  kubectl wait --for=condition=ready pod -l app=frontdeskai --timeout=120s

  echo ""
  echo "==> FrontDesk AI deployed!"
  echo "    App URL: https://${NAMESPACE}-app.brainupgrade.in"
  echo "    Verify:  kubectl get pods -l app=frontdeskai"
fi

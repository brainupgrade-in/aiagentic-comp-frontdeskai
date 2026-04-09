#!/bin/bash
# Deploy FrontDesk AI.
# Auto-detects environment:
#   - kind context  → local image load, NodePort (localhost:8000 — no port-forward)
#   - other context → production sandbox (brainupgrade.in registry + ingress)
#
# Usage: bash scripts/deploy.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MANIFESTS="${REPO_DIR}/scripts/manifests"
ENV_FILE="${REPO_DIR}/.env"
CLUSTER_NAME="frontdeskai"
LOCAL_IMAGE="frontdeskai:latest"

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: .env file not found at ${ENV_FILE}"
  echo "       Copy .env.example to .env and fill in your API keys."
  exit 1
fi

# Source .env file (skip comments and blank lines)
while IFS= read -r line; do
  # Strip carriage return (Windows line endings)
  line="${line//$'\r'/}"
  # Skip blank lines and comments
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  # Split on first = only
  key="${line%%=*}"
  value="${line#*=}"
  # Strip inline comments, surrounding quotes, trailing whitespace from value
  value="${value%%#*}"
  value="${value#\"}" ; value="${value%\"}"
  value="${value#\'}" ; value="${value%\'}"
  value="${value%"${value##*[![:space:]]}"}"
  # Skip if key is empty or contains spaces (malformed line)
  [[ -z "$key" || "$key" =~ [[:space:]] ]] && continue
  export "$key=$value"
done < "${ENV_FILE}"

GROQ_API_KEY="${GROQ_API_KEY:?ERROR: GROQ_API_KEY not set in .env file}"
AUTH_PASSWORD="${AUTH_PASSWORD:?ERROR: AUTH_PASSWORD not set in .env file}"

CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")

# ── Helpers ──────────────────────────────────────────────────────────────────
apply_secret() {
  echo "==> Creating secret..."

  # Preserve existing SECRET_KEY to avoid breaking Fernet-encrypted DB values
  EXISTING_SECRET_KEY=$(kubectl get secret frontdeskai-secret \
    -o jsonpath='{.data.SECRET_KEY}' 2>/dev/null | base64 -d || echo "")
  if [ -z "${EXISTING_SECRET_KEY}" ]; then
    echo "    No existing SECRET_KEY — generating a new one"
    EXISTING_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
  else
    echo "    SECRET_KEY preserved from existing secret"
  fi

  SECRET_ARGS=(
    --from-literal=GROQ_API_KEY="${GROQ_API_KEY}"
    --from-literal=SECRET_KEY="${EXISTING_SECRET_KEY}"
    --from-literal=AUTH_PASSWORD="${AUTH_PASSWORD}"
  )
  if [ -n "${OLLAMA_API_KEY:-}" ]; then
    SECRET_ARGS+=(--from-literal=OLLAMA_API_KEY="${OLLAMA_API_KEY}")
    echo "    Ollama API key included"
  else
    echo "    OLLAMA_API_KEY not set — Ollama Cloud fallback will be disabled"
  fi
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
      "${MANIFESTS}/deployment.yaml" \
    | sed "s|imagePullPolicy: Never|imagePullPolicy: ${pull_policy}|g" \
    > "${DEPLOY_TMP}"
  kubectl apply -f "${DEPLOY_TMP}"
  kubectl apply -f "${MANIFESTS}/service.yaml"
  if kubectl apply -f "${MANIFESTS}/servicemonitor.yaml" 2>/dev/null; then
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

  apply_secret
  apply_manifests "${LOCAL_IMAGE}" "Never"

  echo "==> Restarting deployment to pick up new image..."
  kubectl rollout restart deployment/frontdeskai

  echo "==> Waiting for app to be ready..."
  kubectl rollout status deployment/frontdeskai --timeout=120s

  echo ""
  echo "==> FrontDesk AI deployed to kind!"
  echo "    Access the app:      http://localhost:8000  (NodePort 30800)"
  echo "    Prometheus metrics:  http://localhost:9090  (NodePort 30900)"
  echo "    Verify: kubectl get pods -l app=frontdeskai"

# ── Production sandbox (brainupgrade.in) ─────────────────────────────────────
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

  echo "==> Building and pushing image..."
  docker build -t "${IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"
  docker push "${IMAGE}"

  apply_secret
  apply_manifests "${IMAGE}" "Always"

  echo "==> Restarting deployment to pick up new image..."
  kubectl rollout restart deployment/frontdeskai

  echo "==> Waiting for app to be ready..."
  kubectl rollout status deployment/frontdeskai --timeout=120s

  echo ""
  echo "==> FrontDesk AI deployed!"
  echo "    App URL: https://${NAMESPACE}-app.brainupgrade.in"
  echo "    Verify:  kubectl get pods -l app=frontdeskai"
fi

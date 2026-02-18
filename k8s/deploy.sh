#!/bin/bash
# Deploy FrontDesk AI to your sandbox namespace.
# Deploys registry, builds/pushes image, and deploys the app.
# Reads API keys from .env file (GROQ_API_KEY, LANGFUSE_* keys).
#
# Usage: bash k8s/deploy.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${REPO_DIR}/.env"

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: .env file not found at ${ENV_FILE}"
  echo "       Copy .env.example to .env and fill in your API keys."
  exit 1
fi

# Source .env file (skip comments and blank lines)
while IFS='=' read -r key value; do
  key="${key%%#*}"           # strip inline comments
  key="${key// /}"           # strip whitespace from key
  [[ -z "$key" ]] && continue
  [[ "$key" =~ ^# ]] && continue
  value="${value%%#*}"       # strip inline comments from value
  value="${value#\"}"        # strip leading quote
  value="${value%\"}"        # strip trailing quote
  value="${value%"${value##*[![:space:]]}"}"  # trim trailing whitespace
  export "$key=$value"
done < "${ENV_FILE}"

GROQ_API_KEY="${GROQ_API_KEY:?ERROR: GROQ_API_KEY not set in .env file}"

# Auto-detect namespace
NAMESPACE=$(kubectl config get-contexts "$(kubectl config current-context)" --no-headers | awk '{print $5}')
if [ -z "$NAMESPACE" ]; then
  echo "ERROR: Could not detect namespace from kubectl context."
  exit 1
fi
REGISTRY="${NAMESPACE}-registry.brainupgrade.in"
IMAGE="${REGISTRY}/frontdeskai:latest"

echo "==> Namespace: ${NAMESPACE}"
echo "==> Registry:  ${REGISTRY}"

# 1. Update manifests with namespace
echo "==> Updating manifests..."
sed -i "s/YOURNAMESPACE/${NAMESPACE}/g" "${REPO_DIR}/k8s/deployment.yaml"

# 2. Deploy registry (deployment + service + PVC only; ingress is pre-created by admin)
echo "==> Deploying private registry..."
kubectl apply -f "${REPO_DIR}/k8s/registry.yaml"
kubectl wait --for=condition=ready pod -l app=registry --timeout=120s

# 3. Build and push image
echo "==> Building image..."
docker build -t "${IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"

echo "==> Pushing image to ${REGISTRY}..."
docker push "${IMAGE}"

# 4. Create secret (includes Langfuse keys if set in .env)
echo "==> Creating secret..."
SECRET_ARGS=(
  --from-literal=GROQ_API_KEY="${GROQ_API_KEY}"
  --from-literal=SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
)
if [ -n "${LANGFUSE_SECRET_KEY:-}" ] && [ -n "${LANGFUSE_PUBLIC_KEY:-}" ] && [ -n "${LANGFUSE_HOST:-}" ]; then
  SECRET_ARGS+=(
    --from-literal=LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY}"
    --from-literal=LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY}"
    --from-literal=LANGFUSE_HOST="${LANGFUSE_HOST}"
  )
  echo "    Langfuse keys included"
else
  echo "    Langfuse keys not set, skipping (app will run without Langfuse)"
fi
kubectl create secret generic frontdeskai-secret \
  "${SECRET_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -

# 5. Deploy app
echo "==> Deploying application..."
kubectl apply -f "${REPO_DIR}/k8s/deployment.yaml"
kubectl apply -f "${REPO_DIR}/k8s/service.yaml"

# 5b. Deploy ServiceMonitor if CRD exists and user has permissions
if kubectl apply -f "${REPO_DIR}/k8s/servicemonitor.yaml" 2>/dev/null; then
  echo "==> ServiceMonitor deployed"
else
  echo "==> ServiceMonitor skipped (CRD not found or insufficient permissions)"
  echo "    Prometheus will use pod annotations instead"
fi

# 6. Wait and verify
echo "==> Waiting for app to be ready..."
kubectl wait --for=condition=ready pod -l app=frontdeskai --timeout=120s

echo ""
echo "==> FrontDesk AI deployed successfully!"
echo "    App URL: https://${NAMESPACE}-app.brainupgrade.in"
echo "    Verify:  kubectl get pods -l app=frontdeskai"

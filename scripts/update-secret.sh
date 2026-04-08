#!/bin/bash
# Update the K8s secret from .env without rebuilding the image.
# Preserves the existing SECRET_KEY to avoid breaking encrypted SMTP passwords.
#
# Usage: bash scripts/update-secret.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${REPO_DIR}/.env"

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: .env file not found at ${ENV_FILE}"
  exit 1
fi

# Source .env
while IFS= read -r line; do
  line="${line//$'\r'/}"
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  value="${value%%#*}"
  value="${value#\"}" ; value="${value%\"}"
  value="${value#\'}" ; value="${value%\'}"
  value="${value%"${value##*[![:space:]]}"}"
  [[ -z "$key" || "$key" =~ [[:space:]] ]] && continue
  export "$key=$value"
done < "${ENV_FILE}"

GROQ_API_KEY="${GROQ_API_KEY:?ERROR: GROQ_API_KEY not set in .env}"
AUTH_PASSWORD="${AUTH_PASSWORD:?ERROR: AUTH_PASSWORD not set in .env}"
OLLAMA_API_KEY="${OLLAMA_API_KEY:-}"

# Preserve existing SECRET_KEY to avoid breaking Fernet-encrypted values in DB
echo "==> Preserving existing SECRET_KEY from cluster..."
EXISTING_SECRET_KEY=$(kubectl get secret frontdeskai-secret \
  -o jsonpath='{.data.SECRET_KEY}' 2>/dev/null | base64 -d || echo "")

if [ -z "${EXISTING_SECRET_KEY}" ]; then
  echo "    No existing SECRET_KEY found — generating a new one"
  EXISTING_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
else
  echo "    SECRET_KEY preserved"
fi

SECRET_ARGS=(
  --from-literal=GROQ_API_KEY="${GROQ_API_KEY}"
  --from-literal=AUTH_PASSWORD="${AUTH_PASSWORD}"
  --from-literal=SECRET_KEY="${EXISTING_SECRET_KEY}"
)

if [ -n "${OLLAMA_API_KEY}" ]; then
  SECRET_ARGS+=(--from-literal=OLLAMA_API_KEY="${OLLAMA_API_KEY}")
  echo "==> Ollama API key included"
else
  echo "==> WARNING: OLLAMA_API_KEY not set in .env — Ollama Cloud fallback will be disabled"
fi

# Langfuse — include only if all three vars are set
LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY:-}"
LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY:-}"
LANGFUSE_HOST="${LANGFUSE_HOST:-}"

if [ -n "${LANGFUSE_SECRET_KEY}" ] && [ -n "${LANGFUSE_PUBLIC_KEY}" ] && [ -n "${LANGFUSE_HOST}" ]; then
  SECRET_ARGS+=(
    --from-literal=LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY}"
    --from-literal=LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY}"
    --from-literal=LANGFUSE_HOST="${LANGFUSE_HOST}"
  )
  echo "==> Langfuse keys included"
else
  echo "==> WARNING: Langfuse keys missing in .env — Langfuse will be disabled"
fi

echo "==> Updating secret frontdeskai-secret..."
kubectl create secret generic frontdeskai-secret \
  "${SECRET_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Restarting deployment to pick up new secret..."
kubectl rollout restart deployment/frontdeskai

echo "==> Waiting for rollout..."
kubectl rollout status deployment/frontdeskai --timeout=120s

echo ""
echo "==> Verifying Langfuse status in logs..."
sleep 3
kubectl logs deployment/frontdeskai | grep -i langfuse || echo "    (no langfuse log line yet — try: kubectl logs deployment/frontdeskai | grep -i langfuse)"

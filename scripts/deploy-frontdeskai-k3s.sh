#!/bin/bash
# Deploy FrontDesk AI to a k3s cluster.
#
# Prerequisites:
#   - k3s server running (scripts/install-k3s-server.sh)
#   - Local registry running on port 30500 (scripts/setup-k3s-registry.sh)
#     OR set REGISTRY env var to a reachable registry
#   - .env file with GROQ_API_KEY (and optionally AUTH_PASSWORD, Langfuse keys)
#   - Docker or nerdctl available for building images
#
# Usage:
#   bash scripts/deploy-frontdeskai-k3s.sh
#
# Environment overrides:
#   REGISTRY          Registry host:port  (default: localhost:30500)
#   NAMESPACE         k8s namespace       (default: default)
#   IMAGE_TAG         Image tag           (default: latest)
#   SKIP_BUILD        Skip image build    (default: false)
#   SKIP_PUSH         Skip image push     (default: false)
#   CONTAINER_TOOL    docker or nerdctl   (default: auto-detect)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${REPO_DIR}/.env"

REGISTRY="${REGISTRY:-localhost:30500}"
NAMESPACE="${NAMESPACE:-default}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
SKIP_BUILD="${SKIP_BUILD:-false}"
SKIP_PUSH="${SKIP_PUSH:-false}"
IMAGE="${REGISTRY}/frontdeskai:${IMAGE_TAG}"

# ── Auto-detect container tool ────────────────────────────────────────────────
if [ -z "${CONTAINER_TOOL:-}" ]; then
  if command -v docker &>/dev/null; then
    CONTAINER_TOOL="docker"
  elif command -v nerdctl &>/dev/null; then
    CONTAINER_TOOL="nerdctl"
  else
    echo "ERROR: Neither docker nor nerdctl found. Install one to build images."
    exit 1
  fi
fi

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: .env file not found at ${ENV_FILE}"
  echo "       Copy .env.example to .env and fill in GROQ_API_KEY and AUTH_PASSWORD."
  exit 1
fi

while IFS='=' read -r key value; do
  key="${key%%#*}"; key="${key// /}"
  [[ -z "$key" || "$key" =~ ^# ]] && continue
  value="${value%%#*}"
  value="${value#\"}"; value="${value%\"}"
  value="${value%"${value##*[![:space:]]}"}"
  export "$key=$value"
done < "${ENV_FILE}"

GROQ_API_KEY="${GROQ_API_KEY:?ERROR: GROQ_API_KEY not set in .env}"
AUTH_PASSWORD="${AUTH_PASSWORD:-brainupgrade}"

# ── Namespace ─────────────────────────────────────────────────────────────────
echo "==> Namespace: ${NAMESPACE}"
kubectl get namespace "${NAMESPACE}" &>/dev/null || kubectl create namespace "${NAMESPACE}"

# ── Build image ───────────────────────────────────────────────────────────────
if [ "${SKIP_BUILD}" != "true" ]; then
  echo "==> Building image: ${IMAGE}"
  ${CONTAINER_TOOL} build -t "${IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"
else
  echo "==> Skipping image build (SKIP_BUILD=true)"
fi

# ── Push image ────────────────────────────────────────────────────────────────
if [ "${SKIP_PUSH}" != "true" ]; then
  echo "==> Pushing image to ${REGISTRY}..."
  if [ "${CONTAINER_TOOL}" = "nerdctl" ]; then
    ${CONTAINER_TOOL} push --insecure-registry "${IMAGE}"
  else
    ${CONTAINER_TOOL} push "${IMAGE}"
  fi
else
  echo "==> Skipping image push (SKIP_PUSH=true)"
fi

# ── Alternatively: import directly into k3s containerd (no registry needed) ───
# Uncomment if you want to skip the registry entirely (single-node only):
#   ${CONTAINER_TOOL} save "${IMAGE}" | sudo k3s ctr images import -

# ── Secret ────────────────────────────────────────────────────────────────────
echo "==> Applying secret frontdeskai-secret..."
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
fi
kubectl create secret generic frontdeskai-secret \
  -n "${NAMESPACE}" \
  "${SECRET_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -

# ── PVC + Deployment ──────────────────────────────────────────────────────────
echo "==> Applying deployment manifests..."
sed "s|YOURNAMESPACE-registry.brainupgrade.in|${REGISTRY}|g" \
  "${REPO_DIR}/k8s/deployment.yaml" \
  | kubectl apply -n "${NAMESPACE}" -f -

# ── Service ───────────────────────────────────────────────────────────────────
echo "==> Applying service..."
kubectl apply -n "${NAMESPACE}" -f "${REPO_DIR}/k8s/service.yaml"

# ── Traefik IngressRoute (k3s default ingress) ────────────────────────────────
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "")
APP_HOST="${APP_HOST:-frontdeskai.local}"

echo "==> Applying Traefik IngressRoute for host: ${APP_HOST}..."
kubectl apply -n "${NAMESPACE}" -f - <<EOF
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: frontdeskai
  labels:
    app: frontdeskai
spec:
  entryPoints:
    - web
  routes:
    - match: Host(\`${APP_HOST}\`)
      kind: Rule
      services:
        - name: app
          port: 80
EOF

# Fallback: plain Ingress in case Traefik CRD is not available
kubectl apply -n "${NAMESPACE}" -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: frontdeskai
  labels:
    app: frontdeskai
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
spec:
  rules:
    - host: ${APP_HOST}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: app
                port:
                  number: 80
EOF

# ── ServiceMonitor (optional) ─────────────────────────────────────────────────
if kubectl apply -n "${NAMESPACE}" -f "${REPO_DIR}/k8s/servicemonitor.yaml" 2>/dev/null; then
  echo "==> ServiceMonitor deployed"
else
  echo "==> ServiceMonitor skipped (Prometheus CRD not found)"
fi

# ── Wait for rollout ──────────────────────────────────────────────────────────
echo "==> Waiting for frontdeskai pod to be ready..."
kubectl rollout status deployment/frontdeskai -n "${NAMESPACE}" --timeout=180s

echo ""
echo "======================================================"
echo "  FrontDesk AI deployed to k3s!"
echo "======================================================"
echo ""
echo "  Namespace : ${NAMESPACE}"
echo "  Image     : ${IMAGE}"
echo "  Host      : ${APP_HOST}"
if [ -n "${NODE_IP}" ]; then
  echo "  Node IP   : ${NODE_IP}"
  echo ""
  echo "  Quick test (add to /etc/hosts if needed):"
  echo "    echo '${NODE_IP} ${APP_HOST}' | sudo tee -a /etc/hosts"
  echo "    curl http://${APP_HOST}/health"
fi
echo ""
echo "  Pod status:"
kubectl get pods -n "${NAMESPACE}" -l app=frontdeskai
echo ""

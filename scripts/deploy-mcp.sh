#!/bin/bash
# Deploy MCP Leave Service (PostgreSQL + MCP Leave Server) to kind cluster.
#
# Run once after cluster is up, before (or alongside) scripts/deploy.sh.
# Idempotent — safe to re-run after code changes.
#
# Usage: bash scripts/deploy-mcp.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MCP_DIR="${REPO_DIR}/mcp"
CLUSTER_NAME="frontdeskai"

CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")
if ! echo "${CURRENT_CONTEXT}" | grep -q "kind"; then
  echo "ERROR: This script only supports kind clusters (current context: ${CURRENT_CONTEXT})"
  exit 1
fi

echo "==> kind cluster detected: ${CURRENT_CONTEXT}"

# ── 1. PostgreSQL ────────────────────────────────────────────────────────────

echo ""
echo "==> [1/4] Deploying PostgreSQL (postgres namespace)..."
kubectl apply -f "${MCP_DIR}/postgre/namespace.yaml"
kubectl apply -f "${MCP_DIR}/postgre/secret.yaml"
kubectl apply -f "${MCP_DIR}/postgre/configmap-init.yaml"
kubectl apply -f "${MCP_DIR}/postgre/pvc.yaml"
kubectl apply -f "${MCP_DIR}/postgre/deployment.yaml"
kubectl apply -f "${MCP_DIR}/postgre/service.yaml"

echo "==> Waiting for PostgreSQL to be ready..."
kubectl rollout status deployment/postgres -n postgres --timeout=90s

# ── 2. Build MCP Leave Server ────────────────────────────────────────────────

echo ""
echo "==> [2/4] Building MCP Leave Server image..."
docker build -t mcp-leave:latest -f "${MCP_DIR}/mcp-postgre/Dockerfile" "${MCP_DIR}/mcp-postgre/"

echo "==> Loading image into kind cluster '${CLUSTER_NAME}'..."
kind load docker-image mcp-leave:latest --name "${CLUSTER_NAME}"

# ── 3. Deploy MCP Leave Server ───────────────────────────────────────────────

echo ""
echo "==> [3/4] Deploying MCP Leave Server (postgres namespace)..."
kubectl apply -f "${MCP_DIR}/mcp-postgre/deployment.yaml"
kubectl apply -f "${MCP_DIR}/mcp-postgre/service.yaml"

echo "==> Waiting for MCP Leave Server to be ready..."
kubectl rollout restart deployment/mcp-leave -n postgres
kubectl rollout status deployment/mcp-leave -n postgres --timeout=60s

# ── 4. Smoke test ────────────────────────────────────────────────────────────

echo ""
echo "==> [4/4] Smoke test — calling MCP from default namespace..."
RESULT=$(kubectl run mcp-smoke-test --rm -i --restart=Never \
  --image=curlimages/curl:latest -n default -- \
  curl -s -X POST http://mcp-leave.postgres.svc.cluster.local:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_leave_balance","arguments":{"employee_id":"alice"}},"id":1}' \
  2>/dev/null)

if echo "${RESULT}" | grep -q "Casual Leave"; then
  echo ""
  echo "    MCP response OK:"
  echo "${RESULT}" | grep '"text"' | sed 's/.*"text":"\([^"]*\)".*/\1/' | tr '\\n' '\n'
else
  echo "ERROR: Smoke test failed. Response:"
  echo "${RESULT}"
  exit 1
fi

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "==> MCP Leave Service deployed!"
echo ""
echo "    Cluster DNS  : http://mcp-leave.postgres.svc.cluster.local:8001/mcp"
echo "    PostgreSQL   : postgres.postgres.svc.cluster.local:5432"
echo ""
echo "    Pods:"
kubectl get pods -n postgres
echo ""
echo "    Next: run 'bash scripts/deploy.sh' to deploy/redeploy FrontDesk AI"

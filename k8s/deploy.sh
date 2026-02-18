#!/bin/bash
# Deploy FrontDesk AI to your sandbox namespace.
# Deploys registry, builds/pushes image, and deploys the app.
#
# Usage: bash k8s/deploy.sh <YOUR_GROQ_API_KEY>

set -euo pipefail

GROQ_API_KEY="${1:?Usage: $0 <GROQ_API_KEY>}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

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

# 4. Create secret
echo "==> Creating secret..."
kubectl create secret generic frontdeskai-secret \
  --from-literal=GROQ_API_KEY="${GROQ_API_KEY}" \
  --from-literal=SECRET_KEY="$(head -c 32 /dev/urandom | base64)" \
  --dry-run=client -o yaml | kubectl apply -f -

# 5. Deploy app
echo "==> Deploying application..."
kubectl apply -f "${REPO_DIR}/k8s/deployment.yaml"
kubectl apply -f "${REPO_DIR}/k8s/service.yaml"

# 5b. Deploy ServiceMonitor if CRD exists (Prometheus Operator)
if kubectl api-resources --api-group=monitoring.coreos.com 2>/dev/null | grep -q servicemonitors; then
  echo "==> Deploying ServiceMonitor..."
  kubectl apply -f "${REPO_DIR}/k8s/servicemonitor.yaml"
else
  echo "==> ServiceMonitor CRD not found, skipping (Prometheus will use pod annotations)"
fi

# 6. Wait and verify
echo "==> Waiting for app to be ready..."
kubectl wait --for=condition=ready pod -l app=frontdeskai --timeout=120s

echo ""
echo "==> FrontDesk AI deployed successfully!"
echo "    App URL: https://${NAMESPACE}-app.brainupgrade.in"
echo "    Verify:  kubectl get pods -l app=frontdeskai"

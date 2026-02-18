#!/bin/bash
# Run this script inside the sandbox pod after cloning the repo.
# It builds the container image and pushes to the per-user in-cluster registry.
# Namespace is auto-detected from the current kubectl context.

set -euo pipefail

NAMESPACE=$(kubectl config get-contexts "$(kubectl config current-context)" --no-headers | awk '{print $5}')
if [ -z "$NAMESPACE" ]; then
  echo "ERROR: Could not detect namespace from kubectl context. Set a namespace in your kubeconfig."
  exit 1
fi
echo "==> Detected namespace: ${NAMESPACE}"
REGISTRY="${NAMESPACE}-registry.brainupgrade.in"
IMAGE="${REGISTRY}/frontdeskai:latest"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Building image from ${REPO_DIR}"
docker build -t "${IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"

echo "==> Pushing image to ${REGISTRY}"
docker push "${IMAGE}"

echo "==> Updating k8s manifests with namespace: ${NAMESPACE}"
sed -i "s/YOURNAMESPACE/${NAMESPACE}/g" "${REPO_DIR}/k8s/deployment.yaml" "${REPO_DIR}/k8s/registry.yaml"

echo "==> Done. Deploy with: kubectl apply -f k8s/"

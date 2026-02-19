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

echo "==> Generating k8s manifests with namespace: ${NAMESPACE}"
echo "    Note: Source YAMLs are not modified. Use deploy.sh for full deployment."
echo "    To apply manually:"
echo "      sed 's/YOURNAMESPACE/${NAMESPACE}/g' k8s/deployment.yaml | kubectl apply -f -"
echo "      sed 's/YOURNAMESPACE/${NAMESPACE}/g' k8s/registry.yaml | kubectl apply -f -"

echo "==> Done."

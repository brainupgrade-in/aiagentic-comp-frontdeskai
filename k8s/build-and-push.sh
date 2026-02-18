#!/bin/bash
# Run this script inside the sandbox pod after cloning the repo.
# It builds the container image with Podman and pushes to the per-user in-cluster registry.
#
# Usage: bash k8s/build-and-push.sh <namespace>
#   e.g.: bash k8s/build-and-push.sh mtvlabk8su1

set -euo pipefail

NAMESPACE="${1:?Usage: $0 <namespace>  (e.g. mtvlabk8su1)}"
REGISTRY="${NAMESPACE}-registry.brainupgrade.in"
IMAGE="${REGISTRY}/frontdeskai:latest"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Building image from ${REPO_DIR}"
podman build -t "${IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"

echo "==> Pushing image to ${REGISTRY}"
podman push "${IMAGE}"

echo "==> Done. Update k8s manifests and deploy:"
echo "    sed -i 's/YOURNAMESPACE/${NAMESPACE}/g' k8s/deployment.yaml k8s/registry.yaml"
echo "    kubectl apply -f k8s/"

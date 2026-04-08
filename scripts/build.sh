#!/bin/bash
# Build the container image and either:
#   - Load it directly into a local kind cluster (devcontainer / Codespace), or
#   - Push it to the per-user in-cluster registry (production sandbox).
#
# Usage: bash scripts/build.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER_NAME="frontdeskai"
LOCAL_IMAGE="frontdeskai:latest"

CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")

if echo "${CURRENT_CONTEXT}" | grep -q "kind"; then
  # ── kind (devcontainer / Codespace) ───────────────────────────────────────
  echo "==> Detected kind cluster context: ${CURRENT_CONTEXT}"
  echo "==> Building image: ${LOCAL_IMAGE}"
  docker build -t "${LOCAL_IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"

  echo "==> Loading image into kind cluster '${CLUSTER_NAME}'..."
  kind load docker-image "${LOCAL_IMAGE}" --name "${CLUSTER_NAME}"
  echo "==> Done. Image available inside kind as '${LOCAL_IMAGE}'."
else
  # ── Production sandbox (brainupgrade.in) ──────────────────────────────────
  NAMESPACE=$(kubectl config get-contexts "${CURRENT_CONTEXT}" --no-headers | awk '{print $5}')
  if [ -z "${NAMESPACE}" ]; then
    echo "ERROR: Could not detect namespace from kubectl context."
    exit 1
  fi
  echo "==> Detected namespace: ${NAMESPACE}"
  REGISTRY="${NAMESPACE}-registry.brainupgrade.in"
  IMAGE="${REGISTRY}/frontdeskai:latest"

  echo "==> Building image: ${IMAGE}"
  docker build -t "${IMAGE}" -f "${REPO_DIR}/Containerfile" "${REPO_DIR}"

  echo "==> Pushing image to ${REGISTRY}..."
  docker push "${IMAGE}"
  echo "==> Done."
fi

#!/bin/bash
# Install k3s agent (worker node) and join it to an existing k3s cluster.
#
# Usage (environment variables):
#   sudo K3S_URL=https://<server-ip>:6443 \
#        K3S_TOKEN=<node-token> \
#        bash scripts/install-k3s-agent.sh
#
# Or pass as positional args:
#   sudo bash scripts/install-k3s-agent.sh https://<server-ip>:6443 <node-token>

set -euo pipefail

# ── Root check ────────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (sudo)."
  exit 1
fi

# ── Resolve server URL and token ──────────────────────────────────────────────
K3S_URL="${K3S_URL:-${1:-}}"
K3S_TOKEN="${K3S_TOKEN:-${2:-}}"

if [ -z "${K3S_URL}" ] || [ -z "${K3S_TOKEN}" ]; then
  echo "ERROR: K3S_URL and K3S_TOKEN are required."
  echo ""
  echo "Usage:"
  echo "  sudo K3S_URL=https://<server-ip>:6443 K3S_TOKEN=<token> bash $0"
  echo "  OR"
  echo "  sudo bash $0 https://<server-ip>:6443 <token>"
  exit 1
fi

echo "==> Joining k3s cluster at ${K3S_URL}..."

INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION:-}"
if [ -n "${INSTALL_K3S_VERSION}" ]; then
  export INSTALL_K3S_VERSION
fi

export K3S_URL
export K3S_TOKEN
curl -sfL https://get.k3s.io | sh -s - agent

echo ""
echo "======================================================"
echo "  k3s agent installed and joined the cluster."
echo "======================================================"
echo ""
echo "  Verify on the server node:"
echo "    kubectl get nodes"
echo ""

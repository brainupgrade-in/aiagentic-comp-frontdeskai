#!/bin/bash
# Install k3s server (control-plane / master node) for FrontDesk AI.
#
# Usage:
#   bash scripts/install-k3s-server.sh [--no-traefik] [--disable-local-storage]
#
# After this script completes:
#   - kubeconfig is copied to ~/.kube/config
#   - Node token is printed — copy it to run install-k3s-agent.sh on workers
#
# Tested on: Ubuntu 22.04 / Debian 12 / RHEL 9

set -euo pipefail

INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION:-}"   # pin to a version, e.g. v1.29.4+k3s1
DISABLE_TRAEFIK=false
DISABLE_LOCAL_STORAGE=false

# Parse flags
for arg in "$@"; do
  case "$arg" in
    --no-traefik)            DISABLE_TRAEFIK=true ;;
    --disable-local-storage) DISABLE_LOCAL_STORAGE=true ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--no-traefik] [--disable-local-storage]"
      exit 1
      ;;
  esac
done

# ── Root check ────────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (sudo)."
  exit 1
fi

# ── Build install flags ───────────────────────────────────────────────────────
K3S_ARGS=""
if $DISABLE_TRAEFIK; then
  K3S_ARGS="${K3S_ARGS} --disable traefik"
  echo "==> Traefik will be disabled (deploy your own ingress controller)"
fi
if $DISABLE_LOCAL_STORAGE; then
  K3S_ARGS="${K3S_ARGS} --disable local-storage"
fi

# ── Install k3s ───────────────────────────────────────────────────────────────
echo "==> Installing k3s server..."
if [ -n "${INSTALL_K3S_VERSION}" ]; then
  export INSTALL_K3S_VERSION
fi

curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server ${K3S_ARGS}" sh -

# ── Wait for node to become Ready ─────────────────────────────────────────────
echo "==> Waiting for node to become Ready..."
for i in $(seq 1 30); do
  STATUS=$(k3s kubectl get node --no-headers 2>/dev/null | awk '{print $2}' | head -1)
  if [ "${STATUS}" = "Ready" ]; then
    echo "    Node is Ready."
    break
  fi
  echo "    Attempt ${i}/30 — status: ${STATUS:-pending}"
  sleep 5
done

# ── Copy kubeconfig for the invoking non-root user ────────────────────────────
REAL_USER="${SUDO_USER:-}"
if [ -n "${REAL_USER}" ]; then
  REAL_HOME=$(getent passwd "${REAL_USER}" | cut -d: -f6)
  mkdir -p "${REAL_HOME}/.kube"
  cp /etc/rancher/k3s/k3s.yaml "${REAL_HOME}/.kube/config"
  chown "${REAL_USER}:${REAL_USER}" "${REAL_HOME}/.kube/config"
  chmod 600 "${REAL_HOME}/.kube/config"
  echo "==> kubeconfig written to ${REAL_HOME}/.kube/config"
else
  echo "==> kubeconfig is at /etc/rancher/k3s/k3s.yaml"
  echo "    Copy it to ~/.kube/config and set KUBECONFIG if needed."
fi

# ── Print join token ──────────────────────────────────────────────────────────
NODE_TOKEN=$(cat /var/lib/rancher/k3s/server/node-token)
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "======================================================"
echo "  k3s server installed successfully!"
echo "======================================================"
echo ""
echo "  Server IP  : ${SERVER_IP}"
echo "  Node token : ${NODE_TOKEN}"
echo ""
echo "  To add worker nodes, run on each worker:"
echo "    sudo K3S_URL=https://${SERVER_IP}:6443 \\"
echo "         K3S_TOKEN=${NODE_TOKEN} \\"
echo "         bash scripts/install-k3s-agent.sh"
echo ""
echo "  Next steps:"
echo "    bash scripts/install-k3s-observability.sh   # Prometheus + Grafana + Loki + Tempo + Promtail"
echo "    bash scripts/deploy-frontdeskai-k3s.sh      # Deploy the app"
echo ""
echo "  Cluster status:"
k3s kubectl get nodes
echo ""

#!/usr/bin/env bash
# Local kind cluster setup — equivalent to .devcontainer/setup.sh but for localhost.
# Usage: bash scripts/create-kind-cluster.sh [cluster-name]
#   Default cluster name: orcl
set -euo pipefail

CLUSTER_NAME="${1:-frontdeskai}"

echo "==> Creating /shared/.sqlite directory..."
sudo mkdir -p /shared/.sqlite
sudo chown "$USER":"$USER" /shared/.sqlite

echo "==> Creating kind cluster '${CLUSTER_NAME}'..."
# extraPortMappings bind NodePorts to localhost so no kubectl port-forward is needed:
#   30800 → localhost:8000  (FrontDesk AI)
#   30300 → localhost:3000  (Grafana)
#   30900 → localhost:9090  (Prometheus)
cat <<KINDCFG | kind create cluster --name "${CLUSTER_NAME}" --config=- || echo "kind cluster '${CLUSTER_NAME}' already exists, skipping"
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30800
        hostPort: 8000
        protocol: TCP
      - containerPort: 30300
        hostPort: 3000
        protocol: TCP
      - containerPort: 30900
        hostPort: 9090
        protocol: TCP
KINDCFG

echo "==> Cluster ready:"
kubectl get nodes

echo ""
echo "=========================================================="
echo " FrontDesk AI kind cluster '${CLUSTER_NAME}' ready"
echo "=========================================================="
echo ""
echo " 1. Deploy app:"
echo "      bash scripts/deploy.sh"
echo ""
echo " 2. Install observability stack:"
echo "      bash scripts/install-observability.sh"
echo ""
echo " 3. Access (no port-forward needed):"
echo "      FrontDesk AI → http://localhost:8000"
echo "      Grafana       → http://localhost:3000  (agenticai / agentgrow.io)"
echo "      Prometheus    → http://localhost:9090"
echo ""
echo " 4. Run app locally (without k8s):"
echo "      source .venv/bin/activate && python app/app.py"
echo "=========================================================="

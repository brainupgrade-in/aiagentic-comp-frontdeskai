#!/usr/bin/env bash
# kind cluster setup for FrontDesk AI. Used directly on localhost/cloud-labs and
# called by .devcontainer/setup.sh so both paths create an identical cluster.
# Usage: bash scripts/create-kind-cluster.sh [cluster-name]
#   Default cluster name: frontdeskai
set -euo pipefail

CLUSTER_NAME="${1:-frontdeskai}"
OWNER="$(id -un)"

echo "==> Creating /shared/.sqlite directory..."
sudo mkdir -p /shared/.sqlite
sudo chown "${OWNER}":"${OWNER}" /shared/.sqlite

# extraPortMappings bind NodePorts to localhost so no kubectl port-forward is needed:
#   30800 → localhost:8000  (FrontDesk AI)
#   30300 → localhost:3000  (Grafana)
#   30900 → localhost:9090  (FrontDesk AI metrics — /metrics on the app pod)
# Prometheus and Tempo have no NodePort; reach them with kubectl port-forward.
if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "==> kind cluster '${CLUSTER_NAME}' already exists, skipping creation"
else
  echo "==> Creating kind cluster '${CLUSTER_NAME}'..."
  cat <<KINDCFG | kind create cluster --name "${CLUSTER_NAME}" --config=-
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
fi

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
echo "      FrontDesk AI  → http://localhost:8000"
echo "      Grafana       → http://localhost:3000  (agenticai / agentgrow.io)"
echo "      App metrics   → http://localhost:9090/metrics"
echo ""
echo "    Prometheus UI (no NodePort — needs port-forward):"
echo "      kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9091:9090"
echo ""
echo " 4. Run app locally (without k8s):"
echo "      source .venv/bin/activate && python app/app.py"
echo "=========================================================="

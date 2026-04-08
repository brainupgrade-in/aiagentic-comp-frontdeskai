#!/usr/bin/env bash
set -euo pipefail

echo "==> Installing kind..."
KIND_VERSION="v0.27.0"
curl -Lo /tmp/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
sudo install -o root -g root -m 0755 /tmp/kind /usr/local/bin/kind
rm /tmp/kind

echo "==> Installing Python dependencies..."
pip install --quiet -r app/requirements.txt

echo "==> Creating /shared/.sqlite directory..."
sudo mkdir -p /shared/.sqlite
sudo chown vscode:vscode /shared/.sqlite

echo "==> Creating kind cluster 'frontdeskai'..."
# extraPortMappings bind NodePorts to localhost so Codespace forwardPorts works
# without kubectl port-forward:
#   30800 → localhost:8000  (FrontDesk AI)
#   30300 → localhost:3000  (Grafana)
#   30900 → localhost:9090  (Prometheus)
cat <<'KINDCFG' | kind create cluster --name frontdeskai --config=- || echo "kind cluster already exists, skipping"
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
echo " FrontDesk AI devcontainer ready"
echo "=========================================================="
echo ""
echo " 1. Deploy app to kind:"
echo "      bash scripts/deploy.sh"
echo ""
echo " 2. Install observability stack (Prometheus, Grafana,"
echo "    Loki, Promtail, Tempo — fully correlated):"
echo "      bash scripts/install-observability.sh"
echo ""
echo " 3. Access (no port-forward needed — NodePort + extraPortMappings):"
echo "      FrontDesk AI → http://localhost:8000"
echo "      Grafana       → http://localhost:3000  (admin / admin)"
echo "      Prometheus    → http://localhost:9090"
echo ""
echo " 4. Run app locally (without k8s):"
echo "      python app/app.py"
echo "=========================================================="

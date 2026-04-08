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
kind create cluster --name frontdeskai \
  || echo "kind cluster already exists, skipping"

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
echo " 3. Access Grafana:"
echo "      kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80 &"
echo "      http://localhost:3000  (admin / admin)"
echo ""
echo " 4. Run app locally (without k8s):"
echo "      python app/app.py"
echo "=========================================================="

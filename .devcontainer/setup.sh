#!/usr/bin/env bash
set -euo pipefail

echo "==> Installing kind..."
KIND_VERSION="v0.27.0"
curl -Lo /tmp/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
sudo install -o root -g root -m 0755 /tmp/kind /usr/local/bin/kind
rm /tmp/kind

echo "==> Installing Python dependencies..."
pip install --quiet -r requirements.txt

echo "==> Creating /shared/.sqlite directory..."
sudo mkdir -p /shared/.sqlite
sudo chown vscode:vscode /shared/.sqlite

echo "==> Creating kind cluster 'frontdeskai'..."
kind create cluster --name frontdeskai \
  || echo "kind cluster already exists, skipping"

echo "==> Cluster ready:"
kubectl get nodes

echo ""
echo "FrontDesk AI devcontainer setup complete."
echo "  Run the app:           python app.py"
echo "  Deploy to kind:        bash k8s/deploy.sh"
echo "  Check cluster:         kubectl get all"
echo "  Load image into kind:  kind load docker-image <image> --name frontdeskai"

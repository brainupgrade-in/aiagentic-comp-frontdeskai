#!/usr/bin/env bash
set -euo pipefail

echo "==> Installing kind..."
KIND_VERSION="v0.32.0"
curl -Lo /tmp/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
sudo install -o root -g root -m 0755 /tmp/kind /usr/local/bin/kind
rm /tmp/kind

# App dependencies are NOT installed here on purpose. The app runs only in the
# kind cluster, where Containerfile installs app/requirements.txt into the image.
# Installing them again in the devcontainer would just slow down Codespace
# creation (chromadb + onnxruntime are heavy). Uncomment if you ever want to run
# `python app/app.py` directly in the Codespace, or want Pylance to resolve the
# third-party imports:
# echo "==> Installing Python dependencies..."
# pip install --quiet -r app/requirements.txt

if [ ! -f .env ]; then
  echo "==> Seeding .env from .env.example (set OLLAMA_API_KEY + GROQ_API_KEY)..."
  cp .env.example .env
fi

# Cluster creation lives in scripts/create-kind-cluster.sh so the Codespace and
# localhost paths stay identical — do not duplicate the kind config here.
bash scripts/create-kind-cluster.sh frontdeskai

echo ""
echo "=========================================================="
echo " Devcontainer notes (see the steps printed above)"
echo "=========================================================="
echo " - The app runs in the kind cluster only — deploy it with scripts/deploy.sh."
echo "   App deps are baked into the image by Containerfile and are deliberately"
echo "   NOT installed in this devcontainer, so 'python app/app.py' will not work"
echo "   here (see the commented pip install in .devcontainer/setup.sh)."
echo " - Edit .env to set OLLAMA_API_KEY + GROQ_API_KEY before deploying."
echo "=========================================================="

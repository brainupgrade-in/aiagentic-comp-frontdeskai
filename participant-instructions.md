# FrontDesk AI — Participant Deployment Guide

## Prerequisites

- A Groq API key — get one free at https://console.groq.com
- **Option A (recommended):** GitHub account to launch a Codespace
- **Option B:** Local machine with Docker and Python 3.12+

---

## Option A: GitHub Codespace (Recommended)

### 1. Launch the Codespace

Open the repo on GitHub → **Code → Codespaces → Create codespace on main**

The devcontainer automatically:
- Installs Python 3.12, kubectl, helm, kind, Docker-in-Docker
- Creates a local kind Kubernetes cluster named `frontdeskai`
- Installs all Python dependencies into the system Python
- Creates `/shared/.sqlite` for persistent storage

Wait for the setup to finish (watch the terminal — it prints "Setup complete" when done).

### 2. Configure API Keys

```bash
cp .env.example .env
# Edit .env and set your GROQ_API_KEY
```

### 3. Deploy the App

```bash
bash scripts/deploy.sh
```

Builds the container image, loads it into the kind cluster, creates the Kubernetes secret, and deploys the app.

### 4. Access the App

The kind cluster is created with `extraPortMappings` and the service uses NodePort — no `kubectl port-forward` needed.

Open directly in your browser:
```
http://localhost:8000
```

**Login:** Any email address + password `brainupgrade`
(First login hashes and saves the password per user — change it later via chat)

### 5. Install Observability Stack (Optional)

```bash
bash scripts/install-observability.sh
```

Installs Prometheus, Grafana, Loki, Promtail, and Tempo with full 3-way correlation (metrics ↔ logs ↔ traces).

Access Grafana directly (NodePort — no port-forward needed):
```
http://localhost:3000  (admin / admin)
```

---

## Option B: Local Machine (without Kubernetes)

### 1. Clone and Configure

```bash
git clone https://github.com/brainupgrade-in/aiagentic-comp-frontdeskai.git
cd aiagentic-comp-frontdeskai

python3 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt

cp .env.example .env
# Edit .env and set GROQ_API_KEY
```

### 2. Run

```bash
python app/app.py
# Open http://localhost:8000
```

### 3. Deploy to kind (with Kubernetes)

```bash
# Install kind (if not installed)
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.27.0/kind-linux-amd64 && chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind

# Create kind cluster with NodePort mappings
cat <<'EOF' | kind create cluster --name frontdeskai --config=-
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
EOF

# Deploy app
bash scripts/deploy.sh

# Access: http://localhost:8000  (NodePort — no port-forward needed)
```

---

## Verify Deployment

```bash
kubectl get pods -l app=frontdeskai
kubectl logs deployment/frontdeskai
```

## Redeploy After Code Changes

```bash
bash scripts/build.sh
kubectl rollout restart deployment/frontdeskai
```

---

## Troubleshooting

**Pod stuck in `ImagePullBackOff` or `ErrImageNeverPull`:**
```bash
# Rebuild and reload the image into kind
bash scripts/build.sh
kubectl rollout restart deployment/frontdeskai
```

**App not responding:**
```bash
kubectl logs deployment/frontdeskai
# Check the secret has the API key
kubectl get secret frontdeskai-secret -o jsonpath='{.data.GROQ_API_KEY}' | base64 -d
```

**kind cluster not found:**
```bash
kind get clusters
# Recreate if missing (with NodePort mappings — required for localhost access)
cat <<'EOF' | kind create cluster --name frontdeskai --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30800
        hostPort: 8000
      - containerPort: 30300
        hostPort: 3000
      - containerPort: 30900
        hostPort: 9090
EOF
bash scripts/deploy.sh
```

**Codespace enters recovery mode (Docker-in-Docker fails):**

Ensure the devcontainer base image is `mcr.microsoft.com/devcontainers/python:3.12-bookworm` (not `bullseye`). Delete the Codespace and create a new one to pick up the fix.

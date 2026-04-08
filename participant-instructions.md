# FrontDesk AI — Participant Deployment Guide

## Prerequisites

- GitHub Codespace created from this repo (recommended), **or** a local machine with Docker and kubectl installed
- A Groq API key — get one free at https://console.groq.com

---

## Option A: GitHub Codespace (Recommended)

### 1. Launch the Codespace

Open the repo on GitHub → **Code → Codespaces → Create codespace on main**

The devcontainer automatically:
- Installs Python 3.12, kubectl, helm, kind, Docker-in-Docker
- Creates a local kind Kubernetes cluster named `frontdeskai`
- Installs all Python dependencies

Wait for the setup to finish (watch the terminal).

### 2. Configure API Keys

```bash
cp .env.example .env
# Edit .env and set your GROQ_API_KEY
```

### 3. Deploy the App

```bash
bash scripts/deploy.sh
```

This builds the container image, loads it into the kind cluster, creates the Kubernetes secret, and deploys the app.

### 4. Access the App

```bash
kubectl port-forward svc/frontdeskai 8000:80 &
```

Open the **Ports** tab in VS Code and click the forwarded port 8000, or open:
```
http://localhost:8000
```

**Login:** Any email address + password `brainupgrade`
(First login hashes and saves the password per user — you can change it later via chat)

### 5. Install Observability Stack (Optional)

```bash
bash scripts/install-observability.sh
```

Installs Prometheus, Grafana, Loki, Promtail, and Tempo — fully correlated (metrics ↔ logs ↔ traces).

```bash
# Access Grafana
kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80 &
# Open http://localhost:3000  (admin / admin)
```

---

## Option B: Local Machine

### 1. Clone and Configure

```bash
git clone https://github.com/brainupgrade-in/aiagentic-comp-frontdeskai.git
cd aiagentic-comp-frontdeskai

python3 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt

cp .env.example .env
# Edit .env and set GROQ_API_KEY
```

### 2. Run Locally (without Kubernetes)

```bash
python app/app.py
# Open http://localhost:8000
```

### 3. Deploy to kind (with Kubernetes)

```bash
# Create kind cluster
kind create cluster --name frontdeskai

# Deploy app
bash scripts/deploy.sh

# Access
kubectl port-forward svc/frontdeskai 8000:80 &
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
kubectl describe pod -l app=frontdeskai
# Rebuild and reload the image into kind
bash scripts/build.sh
```

**App not responding:**
```bash
kubectl logs deployment/frontdeskai
# Check the secret has the API key
kubectl get secret frontdeskai-secret -o jsonpath='{.data.GROQ_API_KEY}' | base64 -d
```

**Port-forward drops after a while:**
```bash
kubectl port-forward svc/frontdeskai 8000:80 &
```

**kind cluster not found:**
```bash
kind get clusters
# Recreate if missing
kind create cluster --name frontdeskai
```

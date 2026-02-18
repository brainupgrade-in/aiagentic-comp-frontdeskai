# FrontDesk AI — Multi-Agent Support System

Agentic AI employee support desk powered by LangGraph multi-agent orchestration.

## Architecture

```
User Request → Supervisor (LLM classifier)
                   ↓
        ┌──────────┼──────────┐
        HR    Tech   Finance  Facilities  General  Clarify
        └──────────┼──────────┘
                   ↓
           Escalation Check
           ├→ Manager (policy exceptions)
           ├→ QA Check → Finalize
           └→ Fallback (template)
```

**Agents:** Supervisor, HR Worker, Tech Worker, Finance Worker, Facilities Worker, General Worker, Clarify Agent, Manager, QA Gate, Fallback

**Stack:** FastAPI + LangGraph + Groq (llama-3.3-70b) + SQLite

**Login:** Any email + password `brainupgrade`

## Quick Start (Local)

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env and set GROQ_API_KEY

# Run
python app.py
# Open http://localhost:8000
```

## Build & Deploy on Kubernetes (Sandbox)

This project is designed to run inside a Cloud Lab sandbox environment on an **AWS EKS** cluster. Each participant has their own namespace (`mtvlabk8suN`) with a private in-cluster container registry exposed via Ingress with TLS — no external registry (Docker Hub, ECR) is needed.

### Step 1: Create the secret with your API key

```bash
# Edit k8s/secret.yaml and set your GROQ_API_KEY
kubectl apply -f k8s/secret.yaml
```

### Step 2: Build, push, and deploy

The script auto-detects your namespace from `kubectl config get-contexts`, builds the image, pushes it to your private registry, and updates the manifests:

```bash
bash k8s/build-and-push.sh
kubectl apply -f k8s/
```

### Step 3: Verify

```bash
kubectl get pods -l app=frontdeskai
kubectl logs deployment/frontdeskai
```

## Kubernetes Manifests

| File | Description |
|------|-------------|
| `k8s/registry.yaml` | In-namespace container registry + PVC + Ingress (TLS) |
| `k8s/secret.yaml` | GROQ_API_KEY and SECRET_KEY |
| `k8s/deployment.yaml` | App deployment + 1Gi PVC for SQLite data |
| `k8s/service.yaml` | NodePort service (port 30080) |
| `k8s/build-and-push.sh` | Build and push image to the per-user registry |

## Project Structure

```
├── app.py              # FastAPI application with auth, chat, and history
├── agents.py           # LangGraph multi-agent graph definition
├── requirements.txt    # Python dependencies
├── Containerfile       # Container image (python:3.13-slim)
├── templates/
│   ├── login.html      # Login page
│   └── chat.html       # Chat interface
├── static/
│   └── style.css       # UI styles
├── data/               # Knowledge base / reference data
└── k8s/                # Kubernetes deployment manifests
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GROQ_API_KEY` | Groq API key for LLM access | (required) |
| `SECRET_KEY` | JWT signing secret | `frontdeskai-default-secret-change-me` |
| `SQLITE_DIR` | SQLite database directory | `/shared/.sqlite` |

## Access

- **Local:** http://localhost:8000
- **Kubernetes:** http://NODE_IP:30080
- **Sandbox app ingress:** https://mtvlabk8suN-app.brainupgrade.in

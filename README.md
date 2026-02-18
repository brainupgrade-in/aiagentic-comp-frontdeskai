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

This project is designed to run inside a Cloud Lab sandbox environment on an **AWS EKS** cluster. Each participant has their own namespace with a private in-cluster container registry — no external registry (Docker Hub, ECR) is needed.

```bash
git clone https://github.com/brainupgrade-in/aiagentic-comp-frontdeskai.git
cd aiagentic-comp-frontdeskai
bash k8s/deploy.sh YOUR_GROQ_API_KEY
```

This single command auto-detects your namespace, deploys a private registry, builds and pushes the image, creates the secret, and deploys the app.

See [participant-instructions.md](participant-instructions.md) for the full guide.

### Redeploy After Code Changes

```bash
bash k8s/build-and-push.sh
kubectl rollout restart deployment/frontdeskai
```

### Verify

```bash
kubectl get pods -l app=frontdeskai
kubectl logs deployment/frontdeskai
```

## Kubernetes Manifests

| File | Description |
|------|-------------|
| `k8s/registry.yaml` | In-namespace container registry + PVC (ingress pre-created by admin) |
| `k8s/secret.yaml` | GROQ_API_KEY and SECRET_KEY |
| `k8s/deployment.yaml` | App deployment + 1Gi PVC for SQLite data |
| `k8s/service.yaml` | NodePort service (port 30080) |
| `k8s/deploy.sh` | One-command deploy (registry + build + push + secret + app) |
| `k8s/build-and-push.sh` | Rebuild and push image after code changes |

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

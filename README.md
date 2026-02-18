# FrontDesk AI — Multi-Agent Support System

Agentic AI employee support desk powered by LangGraph multi-agent orchestration (based on Session 9 Lab 8).

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

## Quick Start

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

**Login:** Any email + password `brainupgrade`

## Podman Build & Run

```bash
podman build -t frontdeskai -f Containerfile .
podman run -d --name frontdeskai \
  -p 8000:8000 \
  -v frontdeskai-data:/shared/.sqlite \
  -e GROQ_API_KEY=your-key \
  frontdeskai
```

## Kubernetes Deployment

```bash
# Edit secret with your API key
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# Access via NodePort 30080
```

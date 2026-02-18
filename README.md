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

**Stack:** FastAPI + LangGraph + Groq (llama-3.3-70b) + SQLite + OpenTelemetry

**Observability:** Prometheus metrics + structured JSON logs (Loki) + distributed tracing

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
| `k8s/deployment.yaml` | App deployment + 1Gi PVC + Prometheus scrape annotations |
| `k8s/service.yaml` | Service `app` with http (80) and metrics (9090) ports |
| `k8s/servicemonitor.yaml` | ServiceMonitor for Prometheus Operator cross-namespace discovery |
| `k8s/deploy.sh` | One-command deploy (registry + build + push + secret + app + ServiceMonitor) |
| `k8s/build-and-push.sh` | Rebuild and push image after code changes |

## Project Structure

```
├── app.py              # FastAPI application with auth, chat, and history
├── agents.py           # LangGraph multi-agent graph definition
├── observability.py    # OpenTelemetry metrics, tracing, and JSON logging
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

## Observability

The app exposes OpenTelemetry-based observability out of the box:

### Metrics (Prometheus)

Exposed at `/metrics` on port 8000. Custom metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `frontdeskai_llm_call_duration_seconds` | Histogram | LLM call latency per agent |
| `frontdeskai_llm_tokens_total` | Counter | Total LLM tokens consumed |
| `frontdeskai_category_total` | Counter | Requests by category |
| `frontdeskai_escalations_total` | Counter | Escalated requests |
| `frontdeskai_fallbacks_total` | Counter | Fallback template uses |
| `frontdeskai_agent_errors_total` | Counter | Agent errors |
| `frontdeskai_request_duration_seconds` | Histogram | End-to-end /chat/send latency |

### Logs (Loki)

Structured JSON to stdout — Loki scrapes pod logs automatically. Each line includes `trace_id`, `span_id`, `agent`, `category`.

```bash
# Grafana Loki query
{app="frontdeskai"} | json | level="ERROR"
```

### Tracing

In-process spans with trace_id/span_id correlated in log lines. Parent span `chat.send` wraps the full request; child spans `llm.<agent>` wrap each LLM call.

### Grafana Queries

```promql
# LLM latency by agent (p95)
histogram_quantile(0.95, rate(frontdeskai_llm_call_duration_seconds_bucket[5m]))

# Request rate by category
rate(frontdeskai_category_total[5m])

# Error rate
rate(frontdeskai_agent_errors_total[5m])
```

## Access

- **Local:** http://localhost:8000
- **Sandbox:** https://YOURNAMESPACE-app.brainupgrade.in

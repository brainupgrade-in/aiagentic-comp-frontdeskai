# FrontDesk AI — Multi-Agent Support System

Agentic AI employee support desk powered by LangGraph multi-agent orchestration.

## Architecture

```
User Request → Supervisor (LLM classifier)
                   ↓
        ┌──────────┼───────────────────────────────┐
        HR    Tech   Finance  Facilities  Analytics  Account  Skill Admin  General  Clarify
        └──────────┼───────────────────────────────┘
                   ↓
           Escalation Check
           ├→ Manager (policy exceptions)
           ├→ QA Check (PII redaction) → Finalize
           └→ Fallback (template)
```

**Agents:** Supervisor, HR Worker, Tech Worker, Finance Worker, Facilities Worker, Analytics Worker, Account Worker, Skill Admin Worker, General Worker, Clarify Agent, Manager, QA Gate, Fallback

**Features:**
- Multi-agent routing with structured LLM classification
- RAG-powered policy document retrieval (ChromaDB with ONNX embeddings — no torch/CUDA required)
- ReAct tool-calling loop (up to 3 iterations per worker)
- QA gate with PII detection/redaction and self-correction retry
- Per-user password storage (PBKDF2-HMAC-SHA256, 600k iterations)
- Chat-based password change via the Account agent
- Dynamic skill installation — admins can teach the system new capabilities via chat (web research → code generation → install → immediate availability)
- Admin-configurable LLM model & provider (Groq/OpenRouter) via chat — persists across restarts
- Admin analytics dashboard with visual UI and chat-based tools
- Knowledge base management (upload/delete policy docs)
- Prompt injection guardrails (delimiter-wrapped user input)
- Security headers, input validation

**Stack:** FastAPI + LangGraph + Groq/OpenRouter (admin-configurable) + SQLite + ChromaDB + OpenTelemetry

**Observability:** Prometheus metrics + structured JSON logs (Loki) + distributed tracing + Langfuse (optional)

**Login:** Any email + shared password (default `brainupgrade`). On first login, the password is hashed and stored per-user. Subsequent logins use the stored hash.

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
├── app.py              # FastAPI application with auth, chat, KB management, analytics API
├── agents.py           # LangGraph multi-agent graph (supervisor, workers, QA, escalation)
├── auth.py             # Per-user password hashing (PBKDF2) and storage
├── tools.py            # Domain tool definitions (HR, Tech, Finance, Facilities, Analytics, Account)
├── skills.py           # Dynamic skill registry — load, install, list skills + web research tools
├── rag.py              # RAG pipeline — ChromaDB indexing and retrieval (ONNX embeddings, no torch)
├── observability.py    # OpenTelemetry metrics, tracing, and JSON logging
├── requirements.txt    # Python dependencies
├── Containerfile       # Container image (python:3.13-slim)
├── templates/
│   ├── login.html      # Login page
│   ├── chat.html       # Chat interface with admin controls
│   ├── kb.html         # Knowledge base management (admin)
│   └── analytics.html  # Analytics dashboard (admin)
├── static/
│   └── style.css       # UI styles
├── data/
│   └── policies/       # Policy markdown documents (RAG source)
└── k8s/                # Kubernetes deployment manifests
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GROQ_API_KEY` | Groq API key for LLM access | (required for Groq provider) |
| `OPENROUTER_API_KEY` | OpenRouter API key (if using OpenRouter provider) | — |
| `SECRET_KEY` | JWT signing secret | Auto-generated per session (required in production) |
| `AUTH_PASSWORD` | Shared password for first-time login | `brainupgrade` |
| `ADMIN_EMAILS` | Comma-separated admin emails | `admin@unigps.in` |
| `SQLITE_DIR` | SQLite database directory | `/shared/.sqlite` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (optional) | — |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key (optional) | — |
| `LANGFUSE_HOST` | Langfuse host URL (optional) | — |

## Authentication

FrontDesk AI uses per-user password storage with PBKDF2-HMAC-SHA256 hashing (600k iterations, 32-byte salt):

1. **First login:** Password is verified against the shared `AUTH_PASSWORD` env var, then a hashed copy is stored in the `users` table of `history.db`
2. **Subsequent logins:** Password is verified against the stored per-user hash (shared password no longer works for that user)
3. **Password change:** Users can change their password via the chat interface by saying "I want to change my password" — the Account agent handles this securely using a `ContextVar` to pass the authenticated identity (the LLM cannot influence which user's password is changed)

## Dynamic Skills

Admins can teach FrontDesk AI new capabilities at runtime — no restart required.

**How it works:**
1. Admin says: *"Install a weather lookup skill"*
2. Supervisor routes to `skill_admin` worker (non-admins are denied)
3. The worker uses `search_web` and `fetch_webpage` to research APIs
4. It generates a Python skill file with `SKILL_META` and `@tool` functions
5. `install_skill` validates the code (AST parse, checks for required structure), saves to `/shared/.frontdeskai/skills/`, and loads it immediately
6. The skill's tools become available to domain workers matching the skill's `categories`

**Skill file format:**
```python
SKILL_META = {"name": "weather", "description": "Weather lookup", "categories": ["facilities"]}
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    import urllib.request, json
    resp = urllib.request.urlopen(f"https://wttr.in/{city}?format=j1")
    data = json.loads(resp.read())
    return f"{city}: {data['current_condition'][0]['temp_C']}°C"
```

**Admin commands via chat:**
- *"Install a skill to check weather forecasts"* — researches, generates, and installs
- *"List installed skills"* — shows all loaded skills with their tools and categories

## LLM Configuration

Admins can change the LLM model, provider, and API key at runtime via chat — no restart needed.

**Supported providers:**
- **Groq** (default): `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `mixtral-8x7b-32768`, etc.
- **OpenRouter**: Access 100+ models via `provider/model` format (e.g. `google/gemini-2.0-flash-001`, `anthropic/claude-3.5-sonnet`)

**Admin commands via chat:**
- *"What model are we using?"* — shows current provider, model, temperature, API key status
- *"Change model to llama-3.1-8b-instant"* — switches Groq model
- *"Switch to OpenRouter with google/gemini-2.0-flash-001 and API key sk-or-..."* — switches provider + model + key
- *"Update the API key to gsk_..."* — updates only the API key

Settings persist in the `system_config` table and survive restarts.

## Databases

| Database | Location | Purpose |
|----------|----------|---------|
| `history.db` | `$SQLITE_DIR/history.db` | Chat message history + `users` table (per-user password hashes) |
| `frontdesk_tools.db` | `$SQLITE_DIR/frontdesk_tools.db` | Business data: employees, leave, tickets, expenses, rooms, payslips + system_config (LLM settings) |
| `checkpoints.db` | `$SQLITE_DIR/checkpoints.db` | LangGraph checkpointer state |
| `chroma/` | `$SQLITE_DIR/chroma/` | ChromaDB vector store for RAG |
| `skills/` | `/shared/.frontdeskai/skills/` | Dynamic skill Python files (loaded at startup) |

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

## Security

- Per-user PBKDF2 password hashing (OWASP 2024 compliant)
- JWT tokens with 24h expiry, `httponly` + `samesite=strict` cookies
- Security headers: CSP, X-Frame-Options DENY, nosniff, referrer policy
- Input validation: message length limits, UTF-8 checks, path traversal protection
- Prompt injection guardrails: delimiter-wrapped user input, PII detection/redaction
- Admin access gated by `ADMIN_EMAILS` env var
- ContextVar-based identity for tool calls (server-set, LLM cannot influence)

## Access

- **Local:** http://localhost:8000
- **Sandbox:** https://YOURNAMESPACE-app.brainupgrade.in

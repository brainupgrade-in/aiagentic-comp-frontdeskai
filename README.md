# FrontDesk AI — Self-Evolving Agentic AI Support System

A truly agentic AI employee support desk that teaches itself new capabilities through conversation. Powered by LangGraph multi-agent orchestration with a zero-rebuild architecture — admins install skills, configure APIs, switch LLM providers, and send emails entirely via chat. Skills are researched, code-generated, validated, persisted, and executed at runtime. Everything survives restarts with no redeployment.

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

**What makes it agentic:**
- **Self-teaching** — admins describe a capability in plain English; the system researches APIs, writes Python code, validates it, installs it, and makes it available to employees — all in one conversation
- **Zero-rebuild** — skills (code on disk), configuration (DB), LLM provider, SMTP settings all persist across restarts without redeployment
- **Runtime extensibility** — new tools are injected into domain workers on the fly; the system grows its own capabilities without touching core code
- **End-to-end agentic loop** — Research (web search) → Code generation → AST validation → Install to filesystem → Configure (API keys in DB, encrypted) → Execute via domain workers

**Core features:**
- Multi-agent routing with structured LLM classification
- RAG-powered policy document retrieval (ChromaDB with ONNX embeddings — no torch/CUDA required)
- ReAct tool-calling loop (up to 3 iterations per worker)
- QA gate with PII detection/redaction and self-correction retry
- Per-user password storage (PBKDF2-HMAC-SHA256, 600k iterations)
- Chat-based password change via the Account agent
- Dynamic skill installation with per-skill configuration (API keys, secrets encrypted at rest)
- Admin-configurable LLM model & provider (Ollama Cloud / Groq / OpenRouter) + automatic fallback via chat — persists across restarts
- Admin-configurable SMTP email — configure and send emails via chat (encrypted password storage)
- Admin analytics dashboard with visual UI and chat-based tools
- Knowledge base management (upload/delete policy docs)
- Prompt injection guardrails (delimiter-wrapped user input)
- Security headers, input validation

**Stack:** FastAPI + LangGraph + Ollama Cloud / Groq / OpenRouter (admin-configurable) + SQLite + ChromaDB + OpenTelemetry

**Observability:** Prometheus metrics + structured JSON logs (Loki) + distributed tracing + Langfuse (optional)

**Login:** Any email + shared password (default `brainupgrade`). On first login, the password is hashed and stored per-user. Subsequent logins use the stored hash.

## Quick Start

### Option A — GitHub Codespace (Recommended)

Open the repo on GitHub → **Code → Codespaces → Create codespace on main**

The devcontainer auto-installs Python, kubectl, helm, kind, and spins up a local Kubernetes cluster. When ready:

```bash
cp .env.example .env          # set OLLAMA_API_KEY (primary) + GROQ_API_KEY (fallback)
bash scripts/deploy.sh        # build + deploy to kind + rollout restart
# Open http://localhost:8000  (NodePort — no port-forward needed)
```

### Option B — Local Machine

```bash
git clone https://github.com/brainupgrade-in/aiagentic-comp-frontdeskai.git
cd aiagentic-comp-frontdeskai

python3 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt

cp .env.example .env          # set OLLAMA_API_KEY (primary) + GROQ_API_KEY (fallback)
python app/app.py             # Open http://localhost:8000
```

See [participant-instructions.md](participant-instructions.md) for the full deployment guide including kind cluster setup and observability stack.

See [user-manual.md](user-manual.md) for end-user and admin usage instructions.

### Observability Stack (Prometheus · Grafana · Loki · Promtail · Tempo)

```bash
bash scripts/install-observability.sh
# Open http://localhost:3000  (admin / admin) — NodePort, no port-forward needed
```

Metrics, logs, and traces are fully correlated in Grafana — click a trace ID in a log line to jump to the trace, or click a metric exemplar to see the originating trace.

### Redeploy After Code Changes

```bash
bash scripts/deploy.sh
```

### Verify

```bash
kubectl get pods -l app=frontdeskai
kubectl logs deployment/frontdeskai
```

## Scripts & Manifests

| File | Description |
|------|-------------|
| `scripts/deploy.sh` | One-command deploy — build + load/push + apply manifests, auto-detects kind vs production |
| `scripts/install-observability.sh` | Install Prometheus, Grafana, Loki, Promtail, Tempo via Helm |
| `scripts/generate-test-traffic.sh` | Generate load to populate observability dashboards |
| `scripts/manifests/deployment.yaml` | App deployment + 1Gi PVC + Prometheus scrape annotations |
| `scripts/manifests/service.yaml` | NodePort service — http (80→30800), metrics (9090→30900) |
| `scripts/manifests/secret.yaml` | Secret template for API keys |
| `scripts/manifests/servicemonitor.yaml` | ServiceMonitor for Prometheus Operator |
| `scripts/observability/` | Helm values for the full observability stack |

## Project Structure

```
├── app/
│   ├── app.py              # FastAPI application with auth, chat, KB management, analytics API
│   ├── agents.py           # LangGraph multi-agent graph (supervisor, workers, QA, escalation)
│   ├── auth.py             # Per-user password hashing (PBKDF2) and storage
│   ├── tools.py            # Domain tool definitions (HR, Tech, Finance, Facilities, Analytics, Account, SMTP)
│   ├── skills.py           # Dynamic skill registry — load, install, list skills + web research tools
│   ├── rag.py              # RAG pipeline — ChromaDB indexing and retrieval (ONNX embeddings, no torch)
│   ├── observability.py    # OpenTelemetry metrics, tracing, and JSON logging
│   ├── requirements.txt    # Python dependencies
│   ├── templates/
│   │   ├── login.html      # Login page
│   │   ├── chat.html       # Chat interface with admin controls
│   │   ├── kb.html         # Knowledge base management (admin)
│   │   └── analytics.html  # Analytics dashboard (admin)
│   ├── static/
│   │   └── style.css       # UI styles
│   └── data/
│       └── policies/       # Policy markdown documents (RAG source)
├── scripts/                # Build, deploy, observability install scripts
│   ├── manifests/          # Kubernetes manifests
│   └── observability/      # Helm values for observability stack
├── .devcontainer/          # GitHub Codespaces / devcontainer config
└── Containerfile           # Container image (python:3.13-slim)
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OLLAMA_API_KEY` | Ollama Cloud API key — primary LLM (`api.ollama.com`) | (required) |
| `GROQ_API_KEY` | Groq API key — fallback LLM | (recommended) |
| `OPENROUTER_API_KEY` | OpenRouter API key (if switching to OpenRouter provider) | — |
| `SECRET_KEY` | JWT signing secret | Auto-generated per session (required in production) |
| `AUTH_PASSWORD` | Shared password for first-time login | `brainupgrade` |
| `ADMIN_EMAILS` | Comma-separated admin emails | `admin@unigps.in` |
| `SQLITE_DIR` | SQLite database directory | `/shared/.sqlite` |
| `SEED_DEMO_DATA` | Pre-populate demo employees, tickets, expenses, leave, rooms, payslips | `false` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (optional) | — |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key (optional) | — |
| `LANGFUSE_HOST` | Langfuse host URL (optional) | — |

## Authentication

FrontDesk AI uses per-user password storage with PBKDF2-HMAC-SHA256 hashing (600k iterations, 32-byte salt):

1. **First login:** Password is verified against the shared `AUTH_PASSWORD` env var, then a hashed copy is stored in the `users` table of `history.db`
2. **Subsequent logins:** Password is verified against the stored per-user hash (shared password no longer works for that user)
3. **Password change:** Users can change their password via the chat interface by saying "I want to change my password" — the Account agent handles this securely using a `ContextVar` to pass the authenticated identity (the LLM cannot influence which user's password is changed)

## Dynamic Skills — The Self-Teaching Engine

FrontDesk AI teaches itself new capabilities through conversation. The admin describes what they want, and the system researches, writes code, installs it, configures it, and executes it — all at runtime, zero rebuild required.

### The Full Agentic Loop

```
Admin: "Install a weather lookup skill"
  │
  ▼
1. RESEARCH    — skill_admin agent searches the web for weather APIs
  │               (search_web → fetch_webpage → reads API docs)
  ▼
2. WRITE CODE  — LLM generates a complete Python skill file
  │               (SKILL_META + config_keys + @tool functions + skill_config() reads)
  ▼
3. VALIDATE    — AST parse, check SKILL_META, check @tool decorators
  ▼
4. PERSIST     — Save to /shared/.frontdeskai/skills/weather.py (survives restarts)
  ▼
5. LOAD        — Import into running process, register tools immediately
  │
  ▼
Admin: "Set the API key for weather skill to sk-..."
  │
  ▼
6. CONFIGURE   — Store encrypted in system_config DB table (survives restarts)
  │
  ▼
Employee: "What's the weather in Mumbai?"
  │
  ▼
7. EXECUTE     — Facilities worker gets weather tool injected, reads config, calls API, responds
```

**What persists without rebuild:**
| What | Where | Survives restart |
|------|-------|-----------------|
| Skill code | Filesystem (`/shared/.frontdeskai/skills/*.py`) | Auto-loaded at startup |
| Skill config (API keys, URLs) | SQLite `system_config` table | Available immediately |
| Secrets (API keys, tokens) | SQLite `system_config` (Fernet encrypted) | Decrypted at runtime |
| LLM model/provider | SQLite `system_config` table | Loaded on import |
| SMTP settings | SQLite `system_config` table | Available immediately |

### Skill File Format

Skills are standalone Python files with a standard structure:

```python
from langchain_core.tools import tool
from skills import skill_config

SKILL_META = {
    "name": "weather",
    "description": "Weather lookup using WeatherAPI",
    "categories": ["facilities"],       # which domain workers get this tool
    "config_keys": ["api_key"],          # declares what config is needed
}

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    import urllib.request, json
    api_key = skill_config("weather", "api_key")
    if not api_key:
        return "Weather skill not configured. Ask an admin to: set weather skill api_key"
    resp = urllib.request.urlopen(f"https://api.weatherapi.com/v1/current.json?key={api_key}&q={city}")
    data = json.loads(resp.read())
    return f"{city}: {data['current']['temp_c']}°C, {data['current']['condition']['text']}"
```

### Admin Commands via Chat

| Command | What happens |
|---------|-------------|
| *"Install a skill to check weather"* | Researches APIs, generates code, validates, installs, loads |
| *"List installed skills"* | Shows all skills with tools, categories, and config keys |
| *"Set the API key for weather skill"* | Stores encrypted in DB via `set_skill_config` |
| *"Show config for weather skill"* | Shows configured vs MISSING keys via `get_skill_config` |

## LLM Configuration

Admins can change the LLM model, provider, API key, and fallback at runtime via chat — no restart needed.

**Supported providers:**
- **Ollama Cloud** (primary default): `gemma4:31b`, `qwen3-next:80b`, `deepseek-v3.1:671b`, etc. — hosted at `api.ollama.com`
- **Groq** (fallback default): `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `mixtral-8x7b-32768`, etc.
- **OpenRouter**: Access 100+ models via `provider/model` format (e.g. `google/gemini-2.0-flash-001`, `anthropic/claude-3.5-sonnet`)

**Admin commands via chat:**
- *"What model are we using?"* — shows current provider, model, temperature, fallback, and API key status
- *"Switch to qwen3-next:80b on ollama"* — switches Ollama Cloud model
- *"Change model to llama-3.1-8b-instant on groq"* — switches provider + model
- *"Switch to OpenRouter with google/gemini-2.0-flash-001 and API key sk-or-..."* — switches to OpenRouter
- *"Set fallback to groq llama-3.1-8b-instant"* — configures fallback LLM
- *"Disable fallback"* — removes fallback

Settings persist in the `system_config` table and survive restarts.

## Email / SMTP Configuration

Admins can configure SMTP email settings and send emails via chat — no restart needed. SMTP passwords are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) derived from `SECRET_KEY`.

**Admin commands via chat:**
- *"Show email settings"* — shows current SMTP configuration (password masked)
- *"Configure SMTP with host=email-smtp.us-east-1.amazonaws.com port=587 username=AKIA... password=... from=noreply@domain.com"* — sets up SMTP
- *"Send an email to rajesh@unigps.in about his leave approval"* — sends an email using configured SMTP

**Security notes:**
- SMTP password is encrypted at rest using Fernet symmetric encryption
- Encryption key is derived from `SECRET_KEY` env var via PBKDF2 — if `SECRET_KEY` is rotated, admin must re-run `configure_smtp`
- Password is never shown in tool output (masked or `"*** (encrypted)"`)
- Only admins can configure SMTP or send emails (gated by `skill_admin` routing)

## Databases

| Database | Location | Purpose |
|----------|----------|---------|
| `history.db` | `$SQLITE_DIR/history.db` | Chat message history + `users` table (per-user password hashes) |
| `frontdesk_tools.db` | `$SQLITE_DIR/frontdesk_tools.db` | Business data: employees, leave, tickets, expenses, rooms, payslips + system_config (LLM + SMTP + skill config) |
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
- SMTP password and secret skill config values encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256, key derived from SECRET_KEY)

## Access

No `kubectl port-forward` needed — the kind cluster is created with `extraPortMappings`
and all services use NodePort, so ports bind directly to `localhost` in the Codespace.

| Service | URL | NodePort |
|---------|-----|----------|
| FrontDesk AI | http://localhost:8000 | 30800 |
| Grafana | http://localhost:3000 (admin / admin) | 30300 |
| Prometheus | http://localhost:9090 | 30900 |

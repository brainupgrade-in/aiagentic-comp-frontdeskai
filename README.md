# FrontDesk AI — Self-Evolving Agentic AI Support System

A truly agentic AI employee support desk that teaches itself new capabilities through conversation. Powered by LangGraph multi-agent orchestration with a zero-rebuild architecture — admins install skills, configure APIs, switch LLM providers, and send emails entirely via chat. Skills are researched, code-generated, validated, persisted, and executed at runtime. Everything survives restarts with no redeployment.

## Architecture

```
User Request → Supervisor (LLM classifier + confidence)
                   │
   ┌───────────────┼──────────────────────────────────────────┐
   │               │                                          │
confidence<5   hr · tech · finance · facilities    analytics · account · skill_admin · general
   │               │                                          │
Clarify      RAG Retrieval → Few-shot Retrieval               │
   │               └──────────────► Domain Worker ◄───────────┘
   │                                     ↓
   │                             Escalation Check
   │                             ├→ Manager (policy exceptions) → QA Check
   │                             ├→ QA Check (PII redaction)
   │                             └→ Fallback (template)
   │                                     ↓
   │                       QA pass → Finalize
   │                       QA fail → Retry Worker (once) → Escalation Check
   └────────────────────────────────────► Finalize
```

**Agents:** Supervisor, Clarify Agent, RAG Retrieval, Few-shot Retrieval, HR Worker, Tech Worker, Finance Worker, Facilities Worker, Analytics Worker, Account Worker, Skill Admin Worker, General Worker, Escalation Check, Manager, QA Gate, Retry Worker, Fallback, Finalize (18 graph nodes)

Only `hr`, `tech`, `finance`, and `facilities` pass through RAG and few-shot retrieval. `analytics`, `account`, `skill_admin`, and `general` route straight to their worker. `skill_admin` is admin-gated — non-admins are redirected to `general`.

**What makes it agentic:**
- **Self-teaching** — admins describe a capability in plain English; the system researches APIs, writes Python code, validates it, installs it, and makes it available to employees — all in one conversation
- **Zero-rebuild** — skills (code on disk), configuration (DB), LLM provider, SMTP settings all persist across restarts without redeployment
- **Runtime extensibility** — new tools are injected into domain workers on the fly; the system grows its own capabilities without touching core code
- **End-to-end agentic loop** — Research (web search) → Code generation → AST validation → Install to filesystem → Configure (API keys in DB, encrypted) → Execute via domain workers

**Core features:**
- Multi-agent routing with structured LLM classification + low-confidence clarification
- RAG-powered policy document retrieval (ChromaDB with ONNX embeddings — no torch/CUDA required)
- Few-shot retrieval — similar past Q&A pairs injected into domain worker prompts
- ReAct tool-calling loop (up to 3 iterations per worker)
- QA gate with PII detection/redaction and one self-correction retry
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

## Documentation

This README is the reference: what the system is, how the agent graph is wired, and every knob it exposes.
The two task-oriented guides own their procedures — they are not duplicated here.

| Doc | Use it when you want to… |
|---|---|
| **[participant-instructions.md](participant-instructions.md)** | Deploy it — Codespace, kind, or local; observability stack; MCP stack; troubleshooting |
| **[user-manual.md](user-manual.md)** | Use it — what to type in chat as an employee or admin |
| **[observability.md](observability.md)** | Read the metrics, spans, logs, and Grafana dashboard in detail |
| **[langfuse-setup.md](langfuse-setup.md)** | Wire up LLM-level tracing with Langfuse |
| **[feature/ociconnectivity.md](feature/ociconnectivity.md)** | Understand the OCI integration design |

## Quick Start

```bash
git clone https://github.com/brainupgrade-in/aiagentic-comp-frontdeskai.git
cd aiagentic-comp-frontdeskai
cp .env.example .env          # set OLLAMA_API_KEY (primary) + GROQ_API_KEY (fallback)

# Kubernetes (Codespace or kind) — the supported path
bash scripts/deploy.sh        # build + load into kind + deploy

# …or run it directly against your Python env
python3 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt && python app/app.py
```

Open **http://localhost:8000** and log in with any email + `brainupgrade`.

Full deployment guide, including cluster creation, the observability stack, and troubleshooting:
**[participant-instructions.md](participant-instructions.md)**.

## MCP Demo — Employee Leave via Remote PostgreSQL

FrontDesk AI includes a Model Context Protocol (MCP) demo showing how an LLM agent calls a remote HR system via the standardized MCP protocol, rather than querying a local database directly.

**Architecture:**
```
default namespace                     postgres namespace
─────────────────                     ──────────────────
FrontDesk AI                          MCP Leave Server (port 8001)
  HR Worker                 MCP/HTTP        ↓ psycopg2
  get_leave_balance_from_hr_system ──→  PostgreSQL (port 5432)
  (urllib JSON-RPC POST)                hr schema: employees,
                                         leave_balances, leave_requests
```

Deploy it with `bash scripts/deploy-mcp.sh` (see [participant-instructions.md](participant-instructions.md)).

**Demo:** ask FrontDesk AI *"How many leaves do I have left?"* — the HR worker calls `get_leave_balance_from_hr_system`, which POSTs a JSON-RPC request to `http://mcp-leave.postgres.svc.cluster.local:8001/mcp`, which queries PostgreSQL and returns the live balance.

**MCP transport:** `streamable-http` (stateless) — a single POST carries the JSON-RPC call and returns an SSE-formatted response. No session handshake required.

See [`mcp/mcp-postgre/design.html`](mcp/mcp-postgre/design.html) for the full architecture diagram.

| Path | Description |
|------|-------------|
| `mcp/postgre/` | PostgreSQL K8s manifests (namespace, secret, configmap with HR schema + seed data, pvc, deployment, service) |
| `mcp/mcp-postgre/mcp_leave_server.py` | FastMCP server — `get_leave_balance()` + `get_leave_usage()` tools backed by PostgreSQL |
| `mcp/mcp-postgre/Dockerfile` | `python:3.12-slim` image with `mcp[cli]` + `psycopg2-binary` |
| `mcp/mcp-postgre/design.html` | Architecture design document with SVG diagrams |
| `scripts/deploy-mcp.sh` | One-command MCP stack deploy — PostgreSQL + MCP server + smoke test |

## Scripts & Manifests

| File | Description |
|------|-------------|
| `scripts/create-kind-cluster.sh` | One-time localhost/cloud-labs setup — creates kind cluster `frontdeskai` with NodePort mappings + `/shared/.sqlite` |
| `scripts/deploy.sh` | One-command deploy — build + load/push + apply manifests, auto-detects kind vs production; preserves existing `SECRET_KEY` to avoid breaking encrypted DB values |
| `scripts/deploy-mcp.sh` | Deploy MCP Leave Service (PostgreSQL + MCP server) into `postgres` namespace with smoke test |
| `scripts/update-secret.sh` | Update the K8s secret from `.env` without rebuilding the image (preserves `SECRET_KEY`) |
| `scripts/install-observability.sh` | Install Prometheus, Grafana, Loki, Promtail, Tempo via Helm |
| `scripts/update-observability.sh` | Update individual observability components (`grafana`, `dashboard`, `prometheus`, `loki`, `promtail`, `tempo`) without a full reinstall |
| `scripts/generate-test-traffic.sh` | Generate load to populate observability dashboards |
| `scripts/manifests/deployment.yaml` | App deployment + 1Gi PVC + Prometheus scrape annotations + `MCP_LEAVE_URL` env |
| `scripts/manifests/service.yaml` | NodePort service — http (80→30800), metrics (9090→30900) |
| `scripts/manifests/secret.yaml` | Secret template — `GROQ_API_KEY`, `SECRET_KEY`, `AUTH_PASSWORD`, `OLLAMA_API_KEY`, optional Langfuse keys |
| `scripts/manifests/servicemonitor.yaml` | ServiceMonitor for Prometheus Operator |
| `scripts/observability/` | Helm values for the full observability stack |

## Project Structure

```
├── app/
│   ├── app.py              # FastAPI application with auth, chat, KB management, analytics API
│   ├── agents.py           # LangGraph multi-agent graph (supervisor, workers, QA, escalation)
│   ├── auth.py             # Per-user password hashing (PBKDF2) and storage
│   ├── tools.py            # Domain tool definitions (HR, Tech, Finance, Facilities, Analytics, Account, SMTP)
│   │                       #   └── get_leave_balance_from_hr_system — MCP client tool (JSON-RPC → MCP server)
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
│           ├── hr-handbook.md
│           ├── it-support.md
│           ├── finance-policies.md
│           ├── facilities-guide.md
│           └── oci-access.md
├── skills/                 # Skill source kept in git (copied to the PVC at /shared/.frontdeskai/skills/)
│   ├── oci_compute.py      # OCI compute self-service skill
│   └── oci_compute.md      # Skill reference — tools, config keys, IAM policies
├── feature/                # Feature design docs (e.g. ociconnectivity.md)
├── todo/                   # Implementation plans not yet built
├── mcp/
│   ├── postgre/            # PostgreSQL K8s manifests (namespace, secret, configmap, pvc, deployment, service)
│   └── mcp-postgre/        # MCP Leave Server (FastMCP + psycopg2, streamable-http transport)
│       ├── mcp_leave_server.py   # FastMCP server: get_leave_balance + get_leave_usage tools
│       ├── Dockerfile            # python:3.12-slim image
│       ├── requirements.txt      # mcp[cli] + psycopg2-binary
│       ├── deployment.yaml       # K8s Deployment in postgres namespace
│       ├── service.yaml          # ClusterIP :8001 — mcp-leave.postgres.svc.cluster.local
│       └── design.html           # Architecture design document with SVG diagrams
├── scripts/                # Build, deploy, observability install scripts
│   ├── deploy.sh           # Deploy FrontDesk AI (auto-detects kind vs production)
│   ├── deploy-mcp.sh       # Deploy MCP Leave Service (PostgreSQL + MCP server + smoke test)
│   ├── manifests/          # Kubernetes manifests for FrontDesk AI
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
| `CHROMA_DIR` | ChromaDB persistence directory | `/shared/chromadb` |
| `SEED_DEMO_DATA` | Pre-populate demo employees, tickets, expenses, leave, rooms, payslips | `false` |
| `MCP_LEAVE_URL` | MCP Leave Server endpoint (see MCP Demo above) | `http://mcp-leave.postgres.svc.cluster.local:8001/mcp` |
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

### Skill Admin Tools

The `skill_admin` worker exposes these tools to admins: `search_web`, `fetch_webpage`, `install_skill`,
`list_skills`, `set_skill_config`, `get_skill_config`, `get_llm_config`, `change_llm_model`,
`configure_fallback_llm`, `configure_smtp`, `get_smtp_config`, `send_email`.

For the phrasing that triggers each one, see [user-manual.md](user-manual.md).

### Shipped Skill — `oci_compute`

The repo ships one ready-made skill in `skills/oci_compute.py`: OCI compute self-service for the `tech`
and `skill_admin` workers (list, inspect, softreset/stop/start, launch, terminate instances). Launch and
terminate are admin-gated. All OCI credentials — including the API private key — are set through admin
chat and stored encrypted in `system_config`; no Kubernetes Secret or `~/.oci/config` mount is required.

Install it by copying it onto the PVC (it auto-loads on pod start):

```bash
kubectl cp skills/oci_compute.py \
  $(kubectl get pod -l app=frontdeskai -o jsonpath='{.items[0].metadata.name}'):/shared/.frontdeskai/skills/oci_compute.py
kubectl rollout restart deployment/frontdeskai
```

See [`skills/oci_compute.md`](skills/oci_compute.md) for tools, config keys, and IAM policies, and
[`feature/ociconnectivity.md`](feature/ociconnectivity.md) for the overall OCI integration design.

## LLM Configuration

Admins can change the LLM model, provider, API key, and fallback at runtime via chat — no restart needed.

**Supported providers:**
- **Ollama Cloud** (primary default): `gemma4:cloud`, `qwen3-next:80b`, `deepseek-v3.1:671b`, etc. — hosted at `api.ollama.com`
- **Groq** (fallback default): `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `mixtral-8x7b-32768`, etc.
- **OpenRouter**: Access 100+ models via `provider/model` format (e.g. `google/gemini-2.0-flash-001`, `anthropic/claude-3.5-sonnet`)

Model, provider, API key, and fallback are changed at runtime through admin chat — see
[user-manual.md](user-manual.md) for the exact phrasing. Settings persist in the `system_config`
table and survive restarts. If the primary provider errors, the configured fallback LLM is used
automatically.

## Email / SMTP Configuration

Admins configure SMTP and send email through chat — no restart needed; see
[user-manual.md](user-manual.md). SMTP passwords are encrypted with Fernet (AES-128-CBC +
HMAC-SHA256) derived from `SECRET_KEY`.

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
| ChromaDB | `$CHROMA_DIR` (default `/shared/chromadb`) | Vector store for RAG policy chunks + few-shot examples |
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

Structured JSON to stdout — Promtail ships pod logs to Loki. Each line includes `trace_id`, `span_id`, `agent`, `category`.

### Tracing

Spans are exported over OTLP gRPC to Tempo (`OTEL_EXPORTER_OTLP_ENDPOINT`) and correlated with log lines via `trace_id`/`span_id`. Parent span `chat.send` wraps the full request; child spans `llm.<agent>` wrap each LLM call.

Span attributes, PromQL/LogQL examples, the Grafana dashboard panels, and the known
histogram-bucket and exporter pitfalls are documented in **[observability.md](observability.md)**.
LLM-level tracing (prompts, completions, token cost) is covered in **[langfuse-setup.md](langfuse-setup.md)**.

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

The kind cluster is created with `extraPortMappings`, so services that expose a
NodePort bind directly to `localhost` in the Codespace — no `kubectl port-forward`.

| Service | URL | NodePort |
|---------|-----|----------|
| FrontDesk AI | http://localhost:8000 | 30800 |
| Grafana | http://localhost:3000 (agenticai / agentgrow.io) | 30300 |
| FrontDesk AI metrics | http://localhost:9090/metrics | 30900 |

Prometheus and Tempo have no NodePort — reach them with a port-forward:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9091:9090
```

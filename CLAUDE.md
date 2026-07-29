# CLAUDE.md — FrontDesk AI

## Project Overview

FrontDesk AI is a self-evolving agentic AI employee support desk built with FastAPI, LangGraph, and Ollama Cloud/Groq/OpenRouter LLMs. It routes employee chat requests through a supervisor agent to domain-specific workers (HR, Tech, Finance, Facilities, Analytics, Account, Skill Admin), with RAG-powered policy retrieval, tool-calling, QA checks, and escalation handling.

**Primary LLM:** Ollama Cloud (`api.ollama.com`) — `gemma4:cloud` via `ChatOllama` (`langchain-ollama`). Groq (`llama-3.3-70b-versatile`) is the automatic fallback.

**What makes it agentic:** The system teaches itself new capabilities at runtime — admins describe a skill in plain English, and the system researches APIs, writes Python code, validates it, installs it to disk, configures it (API keys encrypted in DB), and executes it via domain workers. Everything persists across restarts with zero rebuild. Skills, config, LLM provider, and SMTP settings are all managed through conversation.

## Quick Start

### Local (without Kubernetes)

```bash
source .venv/bin/activate
python app/app.py
# Open http://localhost:8000
```

Requires `OLLAMA_API_KEY` (primary) and optionally `GROQ_API_KEY` (fallback) in `.env`.

### GitHub Codespace / kind cluster

Codespace: devcontainer auto-provisions the kind cluster via `.devcontainer/setup.sh`.

```bash
cp .env.example .env          # set OLLAMA_API_KEY + GROQ_API_KEY
bash scripts/deploy.sh        # build + load into kind + deploy + rollout restart
# Open http://localhost:8000  (NodePort 30800 — no port-forward needed)
```

### cloud-labs / localhost (kind)

```bash
bash scripts/create-kind-cluster.sh   # one-time: creates kind cluster 'frontdeskai' + /shared/.sqlite
cp .env.example .env                  # set OLLAMA_API_KEY + GROQ_API_KEY
bash scripts/deploy.sh                # same command as Codespace — auto-detects kind context
# Open http://localhost:8000  (NodePort 30800 — no port-forward needed)
```

**Host prerequisite — passwordless sudo** (required by `create-kind-cluster.sh`):
```bash
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/$USER
```

**Host prerequisite — inotify limits** (required for Promtail — add to `/etc/sysctl.conf` for persistence):
```bash
sudo sysctl fs.inotify.max_user_instances=512
sudo sysctl fs.inotify.max_user_watches=524288
```

### Observability stack (optional)

```bash
bash scripts/install-observability.sh
# Open http://localhost:3000  (agenticai / agentgrow.io) — NodePort 30300, no port-forward needed
```

## Architecture

```
app/
  app.py         (FastAPI)
  ├── auth.py          — per-user password hashing (PBKDF2), ContextVar identity
  ├── agents.py        — LangGraph graph: supervisor → RAG → few-shot → workers → QA → finalize
  │     ├── tools.py   — domain tools (HR, Tech, Finance, Facilities, Analytics, Account)
  │     └── skills.py  — dynamic skill registry, admin tools (search, fetch, install, list)
  ├── rag.py           — ChromaDB vector store, document indexing, retrieval
  ├── fewshot.py       — few-shot memory: successful Q&A pairs in a second Chroma collection
  └── observability.py — Prometheus metrics, structured logging, tracing
```

## Key Files

| File | Purpose |
|------|---------|
| `app/app.py` | FastAPI routes: login, chat, KB management, analytics API, admin gates |
| `app/agents.py` | LangGraph StateGraph: supervisor, 8 domain workers, QA, manager, fallback. Dynamic LLM factory (`get_llm()`) supports Ollama Cloud, Groq, and OpenRouter providers |
| `app/auth.py` | Password hashing (PBKDF2-SHA256, 600k iter), `users` table, `current_user_email` ContextVar |
| `app/tools.py` | LangChain `@tool` functions for each domain + schema/seed data. HR tools include `get_leave_balance_from_hr_system` (MCP client) as the primary leave tool |
| `app/skills.py` | Dynamic skill registry: load/install/list/configure skills, web research tools, `skill_config()` helper, runtime tool injection |
| `app/rag.py` | ChromaDB indexing of `app/data/policies/*.md` into the `unigps_policies` collection, retrieval with category filtering (ONNX embeddings, no torch). Persists to `$CHROMA_DIR` (default `/shared/chromadb`) |
| `app/fewshot.py` | Few-shot memory — successful Q&A pairs in the `fewshot_examples` Chroma collection, retrieved per-category by semantic similarity and injected into domain worker prompts |
| `app/observability.py` | OpenTelemetry metrics, Prometheus exporter, JSON logging, Langfuse integration |
| `mcp/mcp-postgre/mcp_leave_server.py` | FastMCP server (streamable-http, port 8001) — `get_leave_balance` + `get_leave_usage` tools backed by PostgreSQL |
| `mcp/postgre/configmap-init.yaml` | PostgreSQL init SQL — HR schema + 4 seed employees with leave data |
| `scripts/deploy-mcp.sh` | Deploy MCP stack (PostgreSQL + MCP server) into `postgres` namespace, includes smoke test |

## Agent Categories

`hr`, `tech`, `finance`, `facilities`, `analytics`, `account`, `skill_admin`, `general`

- **hr/tech/finance/facilities**: Route through RAG retrieval → few-shot retrieval, then domain worker with tools
- **analytics**: Bypasses retrieval, goes directly to analytics worker with analytics tools
- **account**: Bypasses retrieval, goes directly to account worker with `change_my_password` tool
- **skill_admin**: Bypasses retrieval, admin-only (non-admins routed to general). Tools: `search_web`, `fetch_webpage`, `install_skill`, `list_skills`, `set_skill_config`, `get_skill_config`, `get_llm_config`, `change_llm_model`, `configure_fallback_llm`, `configure_smtp`, `get_smtp_config`, `send_email`. Workers also get dynamically-injected skill tools matching their category. Admins can change the LLM model, provider (ollama/groq/openrouter), API key, fallback LLM, SMTP email settings, and per-skill configuration at runtime via chat.
- **general**: Static response, no tools

Before category routing, the supervisor's confidence gates the graph: a score below 5 routes to the `clarify` node instead of a worker, which asks a follow-up question and goes straight to `finalize`.

## Databases & Storage — Zero-Rebuild Persistence

All runtime state is persisted to survive restarts without redeployment. The three SQLite DBs live in `$SQLITE_DIR` (default `/shared/.sqlite`); the vector store and skills sit alongside it under `/shared`:

- `$SQLITE_DIR/history.db` — chat messages + `users` table (per-user password hashes)
- `$SQLITE_DIR/frontdesk_tools.db` — employees, leave, tickets, expenses, rooms, payslips, system_config (LLM + SMTP + skill config settings, secrets Fernet-encrypted)
- `$SQLITE_DIR/checkpoints.db` — LangGraph checkpointer
- `$CHROMA_DIR` (default `/shared/chromadb`) — ChromaDB: `unigps_policies` (RAG) + `fewshot_examples` collections
- `/shared/.frontdeskai/skills/` — dynamic skill Python files (loaded at startup + on install)

The agentic loop: skill code → filesystem, skill config → DB, LLM/SMTP config → DB. On restart, skills auto-load from disk, config is read from DB — no manual intervention.

## Authentication Flow

1. Login checks `get_user_password(email)` first (per-user hash)
2. If no stored password, verifies against `AUTH_PASSWORD` env var, then saves hash
3. JWT token set as httponly+samesite cookie (24h expiry)
4. `current_user_email` ContextVar set before graph invocation for secure tool identity

## Development Commands

```bash
# Run locally
python app/app.py
# Access: http://localhost:8000 (local) or http://localhost:8000 (kind NodePort — no port-forward)
# Grafana: http://localhost:3000 | App metrics: http://localhost:9090/metrics
# Prometheus UI has no NodePort: kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9091:9090

# Verify auth module
cd app && python -c "from auth import hash_password, verify_password; h,s = hash_password('test'); print(verify_password('test',h,s))"

# Verify graph wiring
cd app && python -c "from agents import build_graph; g = build_graph(); print(sorted(g.nodes))"

# Verify tools
cd app && python -c "from tools import DOMAIN_TOOLS; print(list(DOMAIN_TOOLS.keys()))"

# Verify skills module
cd app && python -c "from skills import load_all_skills; print(load_all_skills())"

# Build and deploy to kind cluster (single command)
bash scripts/deploy.sh       # build + load/push + apply manifests, auto-detects kind vs production

# Redeploy after code changes (kind)
bash scripts/deploy.sh

# Update a secret/config without rebuilding the image
kubectl patch secret frontdeskai-secret \
  --type=merge \
  -p '{"stringData":{"GROQ_API_KEY":"<new-key>"}}'
kubectl rollout restart deployment/frontdeskai

# Check pod status and logs
kubectl get pods -l app=frontdeskai
kubectl logs deployment/frontdeskai
```

## Environment Variables

| Variable | Required | Default |
|----------|----------|---------|
| `OLLAMA_API_KEY` | Yes (primary LLM) | — sign up at https://ollama.com |
| `GROQ_API_KEY` | Recommended (fallback LLM) | — sign up at https://console.groq.com |
| `OPENROUTER_API_KEY` | No (alternative provider) | — |
| `SECRET_KEY` | Production | Auto-generated in dev (also used to derive SMTP password encryption key — if rotated, admin must re-run `configure_smtp`) |
| `AUTH_PASSWORD` | No | `brainupgrade` |
| `ADMIN_EMAILS` | No | `admin@unigps.in` |
| `SQLITE_DIR` | No | `/shared/.sqlite` |
| `CHROMA_DIR` | No | `/shared/chromadb` |
| `MCP_LEAVE_URL` | No | `http://mcp-leave.postgres.svc.cluster.local:8001/mcp` (set in `deployment.yaml`) |
| `OTEL_SERVICE_NAME` | No | `frontdeskai` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `http://tempo.monitoring.svc.cluster.local:4317` |
| `LOG_LEVEL` | No | `INFO` |
| `SEED_DEMO_DATA` | No | `false` — set to `true` to pre-populate employees, tickets, expenses, leave, rooms, payslips for demos |

**LLM defaults (overridable at runtime via admin chat):**
- Primary: `ollama` / `gemma4:cloud` (Ollama Cloud — `https://api.ollama.com`)
- Fallback: `groq` / `llama-3.3-70b-versatile` (auto-used on primary failure)

Note: All runtime configuration is managed through chat (stored in `system_config` table, persists across restarts with zero rebuild): LLM model/provider/API key, fallback LLM, SMTP email settings, and per-skill configuration (API keys, base URLs, etc.). SMTP password and secret skill config values are encrypted with Fernet (derived from `SECRET_KEY`).

**Updating API keys — two options (no image rebuild required):**
1. **Zero-downtime via admin chat** — login as admin and say `update ollama api key to <key>`; the `skill_admin` worker saves it to DB and hot-reloads immediately.
2. **Script** — `bash scripts/update-secret.sh` reads all keys from `.env`, patches the K8s secret, and restarts the pod.

## Agentic Loop (Self-Teaching)

The full agentic cycle for adding a new capability, entirely via chat:
1. **Research** — `search_web` + `fetch_webpage` to discover APIs/approaches
2. **Write code** — LLM generates Python with `SKILL_META`, `config_keys`, `@tool` functions, `skill_config()` reads
3. **Validate** — AST parse, check for `SKILL_META` dict and `@tool` decorators
4. **Persist** — Save `.py` file to `/shared/.frontdeskai/skills/` (survives restart)
5. **Load** — Import module, register tools into `_loaded_skills` registry
6. **Configure** — Admin sets API keys via `set_skill_config` → encrypted in `system_config` DB
7. **Execute** — Domain workers get skill tools injected at invocation time, `skill_config()` reads config from DB

## MCP Leave Service

Employee leave queries are handled by a remote MCP server in the `postgres` namespace backed by PostgreSQL.

```
default namespace                        postgres namespace
─────────────────                        ──────────────────
HR Worker                    MCP/HTTP    MCP Leave Server (:8001)
get_leave_balance_from_hr_system  ──────→  FastMCP · streamable-http
(urllib JSON-RPC POST)                         ↓ psycopg2
                                         PostgreSQL (:5432)
                                         hr schema · seed data
```

**Deploy:** `bash scripts/deploy-mcp.sh` (PostgreSQL + MCP server + smoke test)

**MCP_LEAVE_URL:** `http://mcp-leave.postgres.svc.cluster.local:8001/mcp` — set as env var in `scripts/manifests/deployment.yaml`

**Transport:** `streamable-http` (stateless) — no session handshake, single POST per tool call.

**Key settings applied in `mcp_leave_server.py`:**
- `mcp.settings.stateless_http = True` — no session ID required per call
- `TransportSecuritySettings(enable_dns_rebinding_protection=False)` — allows K8s pod-to-pod DNS

## Scripts & Manifests

| File | Description |
|------|-------------|
| `scripts/create-kind-cluster.sh` | One-time localhost/cloud-labs setup — creates kind cluster `frontdeskai` with NodePort mappings + `/shared/.sqlite`; equivalent of `.devcontainer/setup.sh` for non-Codespace hosts |
| `scripts/deploy.sh` | One-command deploy — build + load/push + apply manifests + rollout restart, auto-detects kind vs production; preserves existing `SECRET_KEY` to avoid breaking encrypted DB values |
| `scripts/deploy-mcp.sh` | Deploy MCP Leave Service — PostgreSQL + MCP server into `postgres` namespace + smoke test |
| `scripts/update-secret.sh` | Update K8s secret from `.env` without rebuilding image (preserves SECRET_KEY, includes OLLAMA_API_KEY + GROQ_API_KEY + Langfuse) |
| `scripts/install-observability.sh` | Install Prometheus, Grafana, Loki, Promtail, Tempo via Helm into `monitoring` namespace |
| `scripts/update-observability.sh` | Update one component without a full reinstall — `grafana`, `dashboard`, `prometheus`, `loki`, `promtail`, `tempo`, or no arg for everything |
| `scripts/generate-test-traffic.sh` | Generate load to populate observability dashboards |
| `skills/oci_compute.py` | OCI compute self-service skill (git-tracked source; `kubectl cp` it to `/shared/.frontdeskai/skills/` to install) |
| `scripts/manifests/deployment.yaml` | App deployment (image: `frontdeskai:latest`, imagePullPolicy: Never for kind) + `MCP_LEAVE_URL` env |
| `scripts/manifests/service.yaml` | NodePort service — http(80→30800), metrics(9090→30900) |
| `scripts/manifests/secret.yaml` | Secret template — `GROQ_API_KEY`, `SECRET_KEY`, `AUTH_PASSWORD`, `OLLAMA_API_KEY`, optional Langfuse keys |
| `scripts/manifests/servicemonitor.yaml` | ServiceMonitor for Prometheus Operator |
| `scripts/observability/tempo.yaml` | Tempo Helm values — OTLP gRPC (4317) + HTTP (4318) receivers |
| `scripts/observability/loki.yaml` | Loki Helm values — SingleBinary, filesystem storage |
| `scripts/observability/promtail.yaml` | Promtail Helm values — JSON log parsing, trace_id label extraction |
| `scripts/observability/kube-prometheus-stack.yaml` | Grafana + Prometheus Helm values — datasources with full 3-way correlation |

## Deployment — kind cluster (Codespace / cloud-labs / local)

**Cluster name:** always `frontdeskai`. Both setup paths converge on this name so `deploy.sh` works unchanged.

**Codespace** — devcontainer (`.devcontainer/`) provisions automatically:
- Base image: `mcr.microsoft.com/devcontainers/python:3.13-bookworm` — Python minor kept in sync with `Containerfile` (`python:3.13-slim`); Bookworm required (Bullseye has an expired Yarn GPG key that breaks the Docker-in-Docker install)
- Features: `docker-in-docker:2`, `kubectl-helm-minikube:1`
- `postCreateCommand`: `.devcontainer/setup.sh` — installs kind binary, seeds `.env`, then delegates to `scripts/create-kind-cluster.sh` (cluster + `/shared/.sqlite`). App deps are deliberately **not** pip-installed here; they live in the image built from `Containerfile`, so the app runs only in the kind cluster.

**cloud-labs / localhost** — run once manually:
```bash
bash scripts/create-kind-cluster.sh   # creates cluster + /shared/.sqlite, uses $USER for chown
```

**`scripts/deploy.sh`** auto-detects environment via `kubectl config current-context`:
- **kind** (context contains `kind`): `imagePullPolicy=Never`, `kind load docker-image --name frontdeskai`
- **production**: `imagePullPolicy=Always`, pushes to `${NAMESPACE}-registry.brainupgrade.in`

**Known host issues (cloud-labs):**
- `sudo` prompts: add user to `/etc/sudoers.d/$USER` with `NOPASSWD:ALL`
- Promtail `CrashLoopBackOff` (`too many open files`): bump inotify limits — `fs.inotify.max_user_instances=512`, `fs.inotify.max_user_watches=524288`; add to `/etc/sysctl.conf` for persistence

## Observability Stack

Full 3-way correlation: Prometheus metrics ↔ Loki logs ↔ Tempo traces, all linked via `trace_id`.

- **Traces**: OTLP gRPC → Tempo (port 4317) via `BatchSpanProcessor`
- **Metrics**: `/metrics` endpoint → Prometheus scrape (pod annotations required)
- **Logs**: structured JSON → stdout → Promtail → Loki (trace_id extracted as label)
- **Grafana**: datasources configured with derived fields (Loki→Tempo), exemplars (Prometheus→Tempo), tracesToLogs (Tempo→Loki)

Pod annotations required for Prometheus scraping (already in `deployment.yaml`):
```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "8000"
prometheus.io/path: "/metrics"
```

## Conventions

- Tools use `@tool` decorator from `langchain_core.tools`
- Domain tools are registered in `DOMAIN_TOOLS` dict in `tools.py`
- Workers are created via `make_domain_worker()` factory in `agents.py`
- New agent categories require updates to: `Classification.category` Literal, supervisor prompt, `WORKER_CONFIGS`, worker creation, `_WORKER_FNS`, `build_graph()`, `_VALID_CATEGORIES` in `app.py`, and fallback templates
- Dynamic skills are Python files in `SKILLS_DIR` with `SKILL_META` dict and `@tool` functions; they are auto-loaded at startup and can be installed at runtime by admins via chat
- Skill tools are injected into domain workers at invocation time based on the skill's `categories` list — zero overhead when no skills target a category
- Skills declare needed config via `config_keys` in `SKILL_META` and read values at runtime via `skill_config(skill_name, key)` from `skills.py`
- Skill config is stored in `system_config` with `skill.{name}.{key}` namespacing; secrets use `_enc` suffix and Fernet encryption
- Admin routes use `_require_admin()` helper, gated by `ADMIN_EMAILS`
- User input in prompts is wrapped in `[USER_REQUEST_START]`/`[USER_REQUEST_END]` delimiters
- All SQL uses parameterized queries; column names are never constructed from user input
- `app/app.py` uses `__file__`-relative paths for `StaticFiles` and `Jinja2Templates` (required when running from repo root as `python app/app.py`)

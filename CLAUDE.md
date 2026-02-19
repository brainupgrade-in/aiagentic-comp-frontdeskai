# CLAUDE.md — FrontDesk AI

## Project Overview

FrontDesk AI is a self-evolving agentic AI employee support desk built with FastAPI, LangGraph, and Groq/OpenRouter LLMs. It routes employee chat requests through a supervisor agent to domain-specific workers (HR, Tech, Finance, Facilities, Analytics, Account, Skill Admin), with RAG-powered policy retrieval, tool-calling, QA checks, and escalation handling.

**What makes it agentic:** The system teaches itself new capabilities at runtime — admins describe a skill in plain English, and the system researches APIs, writes Python code, validates it, installs it to disk, configures it (API keys encrypted in DB), and executes it via domain workers. Everything persists across restarts with zero rebuild. Skills, config, LLM provider, and SMTP settings are all managed through conversation.

## Quick Start

```bash
source .venv/bin/activate
python app.py
# Open http://localhost:8000
```

Requires `GROQ_API_KEY` in `.env` file.

## Architecture

```
app.py (FastAPI)
  ├── auth.py          — per-user password hashing (PBKDF2), ContextVar identity
  ├── agents.py        — LangGraph graph: supervisor → RAG → workers → QA → finalize
  │     ├── tools.py   — domain tools (HR, Tech, Finance, Facilities, Analytics, Account)
  │     └── skills.py  — dynamic skill registry, admin tools (search, fetch, install, list)
  ├── rag.py           — ChromaDB vector store, document indexing, retrieval
  └── observability.py — Prometheus metrics, structured logging, tracing
```

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI routes: login, chat, KB management, analytics API, admin gates |
| `agents.py` | LangGraph StateGraph: supervisor, 8 domain workers, QA, manager, fallback. Dynamic LLM factory (`get_llm()`) supports Groq and OpenRouter providers |
| `auth.py` | Password hashing (PBKDF2-SHA256, 600k iter), `users` table, `current_user_email` ContextVar |
| `tools.py` | LangChain `@tool` functions for each domain + schema/seed data |
| `skills.py` | Dynamic skill registry: load/install/list/configure skills, web research tools, `skill_config()` helper, runtime tool injection |
| `rag.py` | ChromaDB indexing of `data/policies/*.md`, retrieval with category filtering (ONNX embeddings, no torch) |
| `observability.py` | OpenTelemetry metrics, Prometheus exporter, JSON logging, Langfuse integration |

## Agent Categories

`hr`, `tech`, `finance`, `facilities`, `analytics`, `account`, `skill_admin`, `general`

- **hr/tech/finance/facilities**: Route through RAG retrieval, then domain worker with tools
- **analytics**: Bypasses RAG, goes directly to analytics worker with analytics tools
- **account**: Bypasses RAG, goes directly to account worker with `change_my_password` tool
- **skill_admin**: Bypasses RAG, admin-only (non-admins routed to general). Tools: `search_web`, `fetch_webpage`, `install_skill`, `list_skills`, `set_skill_config`, `get_skill_config`, `get_llm_config`, `change_llm_model`, `configure_smtp`, `get_smtp_config`, `send_email`. Workers also get dynamically-injected skill tools matching their category. Admins can change the LLM model, provider (groq/openrouter), API key, SMTP email settings, and per-skill configuration at runtime via chat.
- **general**: Static response, no tools

## Databases & Storage — Zero-Rebuild Persistence

All runtime state is persisted to survive restarts without redeployment (all in `$SQLITE_DIR`, default `/shared/.sqlite`):

- `history.db` — chat messages + `users` table (per-user password hashes)
- `frontdesk_tools.db` — employees, leave, tickets, expenses, rooms, payslips, system_config (LLM + SMTP + skill config settings, secrets Fernet-encrypted)
- `checkpoints.db` — LangGraph checkpointer
- `/shared/.frontdeskai/skills/` — dynamic skill Python files (loaded at startup + on install)

The agentic loop: skill code → filesystem, skill config → DB, LLM/SMTP config → DB. On restart, skills auto-load from disk, config is read from DB — no manual intervention.

## Authentication Flow

1. Login checks `get_user_password(email)` first (per-user hash)
2. If no stored password, verifies against `AUTH_PASSWORD` env var, then saves hash
3. JWT token set as httponly+samesite cookie (24h expiry)
4. `current_user_email` ContextVar set before graph invocation for secure tool identity

## Common Development Commands

```bash
# Run locally
python app.py

# Verify auth module
python -c "from auth import hash_password, verify_password; h,s = hash_password('test'); print(verify_password('test',h,s))"

# Verify graph wiring
python -c "from agents import build_graph; g = build_graph(); print(sorted(g.nodes))"

# Verify tools
python -c "from tools import DOMAIN_TOOLS; print(list(DOMAIN_TOOLS.keys()))"

# Verify skills module
python -c "from skills import load_all_skills; print(load_all_skills())"

# Build and deploy to K8s
bash k8s/build-and-push.sh
kubectl rollout restart deployment/frontdeskai
```

## Environment Variables

| Variable | Required | Default |
|----------|----------|---------|
| `GROQ_API_KEY` | Yes (for Groq) | — |
| `OPENROUTER_API_KEY` | No (for OpenRouter) | — |
| `SECRET_KEY` | Production | Auto-generated in dev (also used to derive SMTP password encryption key — if rotated, admin must re-run `configure_smtp`) |
| `AUTH_PASSWORD` | No | `brainupgrade` |
| `ADMIN_EMAILS` | No | `admin@unigps.in` |
| `SQLITE_DIR` | No | `/shared/.sqlite` |

Note: All runtime configuration is managed through chat (stored in `system_config` table, persists across restarts with zero rebuild): LLM model/provider/API key, SMTP email settings, and per-skill configuration (API keys, base URLs, etc.). SMTP password and secret skill config values are encrypted with Fernet (derived from `SECRET_KEY`).

## Agentic Loop (Self-Teaching)

The full agentic cycle for adding a new capability, entirely via chat:
1. **Research** — `search_web` + `fetch_webpage` to discover APIs/approaches
2. **Write code** — LLM generates Python with `SKILL_META`, `config_keys`, `@tool` functions, `skill_config()` reads
3. **Validate** — AST parse, check for `SKILL_META` dict and `@tool` decorators
4. **Persist** — Save `.py` file to `/shared/.frontdeskai/skills/` (survives restart)
5. **Load** — Import module, register tools into `_loaded_skills` registry
6. **Configure** — Admin sets API keys via `set_skill_config` → encrypted in `system_config` DB
7. **Execute** — Domain workers get skill tools injected at invocation time, `skill_config()` reads config from DB

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

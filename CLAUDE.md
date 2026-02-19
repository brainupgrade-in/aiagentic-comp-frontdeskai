# CLAUDE.md — FrontDesk AI

## Project Overview

FrontDesk AI is a multi-agent employee support desk built with FastAPI, LangGraph, and Groq (llama-3.3-70b). It routes employee chat requests through a supervisor agent to domain-specific workers (HR, Tech, Finance, Facilities, Analytics, Account), with RAG-powered policy retrieval, tool-calling, QA checks, and escalation handling.

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
  │     └── tools.py   — domain tools (HR, Tech, Finance, Facilities, Analytics, Account)
  ├── rag.py           — ChromaDB vector store, document indexing, retrieval
  └── observability.py — Prometheus metrics, structured logging, tracing
```

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI routes: login, chat, KB management, analytics API, admin gates |
| `agents.py` | LangGraph StateGraph: supervisor, 7 domain workers, QA, manager, fallback |
| `auth.py` | Password hashing (PBKDF2-SHA256, 600k iter), `users` table, `current_user_email` ContextVar |
| `tools.py` | LangChain `@tool` functions for each domain + schema/seed data |
| `rag.py` | ChromaDB indexing of `data/policies/*.md`, retrieval with category filtering |
| `observability.py` | OpenTelemetry metrics, Prometheus exporter, JSON logging, Langfuse integration |

## Agent Categories

`hr`, `tech`, `finance`, `facilities`, `analytics`, `account`, `general`

- **hr/tech/finance/facilities**: Route through RAG retrieval, then domain worker with tools
- **analytics**: Bypasses RAG, goes directly to analytics worker with analytics tools
- **account**: Bypasses RAG, goes directly to account worker with `change_my_password` tool
- **general**: Static response, no tools

## Databases (all in `$SQLITE_DIR`, default `/shared/.sqlite`)

- `history.db` — chat messages + `users` table (per-user password hashes)
- `frontdesk_tools.db` — employees, leave, tickets, expenses, rooms, payslips
- `checkpoints.db` — LangGraph checkpointer

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

# Build and deploy to K8s
bash k8s/build-and-push.sh
kubectl rollout restart deployment/frontdeskai
```

## Environment Variables

| Variable | Required | Default |
|----------|----------|---------|
| `GROQ_API_KEY` | Yes | — |
| `SECRET_KEY` | Production | Auto-generated in dev |
| `AUTH_PASSWORD` | No | `brainupgrade` |
| `ADMIN_EMAILS` | No | `admin@unigps.in` |
| `SQLITE_DIR` | No | `/shared/.sqlite` |

## Conventions

- Tools use `@tool` decorator from `langchain_core.tools`
- Domain tools are registered in `DOMAIN_TOOLS` dict in `tools.py`
- Workers are created via `make_domain_worker()` factory in `agents.py`
- New agent categories require updates to: `Classification.category` Literal, supervisor prompt, `WORKER_CONFIGS`, worker creation, `_WORKER_FNS`, `build_graph()`, `_VALID_CATEGORIES` in `app.py`, and fallback templates
- Admin routes use `_require_admin()` helper, gated by `ADMIN_EMAILS`
- User input in prompts is wrapped in `[USER_REQUEST_START]`/`[USER_REQUEST_END]` delimiters
- All SQL uses parameterized queries; column names are never constructed from user input

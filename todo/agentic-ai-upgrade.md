# FrontDesk AI — Agentic AI Upgrade Plan

**Date:** 2026-02-19
**Status:** Planning
**Goal:** Transform FrontDesk AI from a prompt-chaining workflow into a true agentic AI system

---

## Review Summary

### Current State

FrontDesk AI is a multi-agent employee support desk built with FastAPI + LangGraph + Groq (Llama 3.3 70B). It routes queries through a supervisor → specialized workers (HR, Tech, Finance, Facilities) → escalation/QA → finalize pipeline. It has excellent observability (OTel + Prometheus + Langfuse).

### Architecture Strengths

- Clean supervisor/router pattern — one of the 4 core agentic design patterns
- Bounded autonomy with escalation paths, QA gates, and fallback templates
- Full observability stack: Prometheus metrics, distributed tracing, structured JSON logs, Langfuse
- K8s-ready: health probes, PVC, Prometheus scrape annotations, ServiceMonitor
- Audit trail tracking across all agent nodes

### Critical Gaps vs. Modern Agentic AI

| # | Gap | Impact |
|---|-----|--------|
| 1 | **No Tool Use / Function Calling** — agents can't look up data, query APIs, create tickets | Agents are chatbots, not agents. Can't take real actions or retrieve real data |
| 2 | **No RAG / Knowledge Grounding** — all domain knowledge hardcoded in prompt strings | Knowledge can't be updated without code changes. No source citations. Hallucination risk |
| 3 | **No Conversation Memory** — each message is a fresh invocation with no prior context | User saying "yes, that one" after a clarification gets a brand-new context |
| 4 | **No Reasoning Loop (ReAct)** — single-pass DAG, agents never self-correct or retry | Not truly agentic. Can't gather additional info or change strategy mid-task |
| 5 | **No Structured Output** — supervisor parses LLM output with fragile string splitting | Unreliable classification, breaks on unexpected LLM output formats |
| 6 | **Static Prompts** — f-strings with no few-shot examples, no message role separation | Inconsistent output quality, no prompt versioning |

### Agentic AI Maturity Comparison

| Capability | FrontDesk AI Now | Target (Modern Agentic AI) |
|---|---|---|
| Multi-agent routing | Supervisor + 5 workers | Same (already good) |
| Tool use / Function calling | None | APIs, DBs, search, ticket creation |
| RAG / Knowledge retrieval | None (hardcoded) | Agentic RAG with vector store |
| Conversation memory | None (stateless) | Multi-turn with history |
| Reasoning loops (ReAct) | None (single-pass) | Think-Act-Observe loop |
| Structured output | String parsing | Pydantic models / function calling |
| Self-correction | Fallback templates only | Retry, rewrite, alternate strategy |
| QA / Guardrails | Basic (length check) | Content filters, PII detection |
| Observability | Excellent | Already on par |

### Existing Bugs Found

| Issue | Location | Severity |
|---|---|---|
| Hardcoded password `AUTH_PASSWORD = "brainupgrade"` | `app.py:24` | High |
| Default JWT secret in source code | `app.py:23` | High |
| `sed -i` mutates source YAML — breaks on second deploy | `k8s/deploy.sh:48` | High |
| SQLite conn not closed on error in `/chat/send` | `app.py:179-254` | Medium |
| Blocking `compiled.invoke()` in async endpoint | `app.py:216` | Medium |
| Supervisor error swallows exception (doesn't log `e`) | `agents.py:68-74` | Low |
| No `imagePullPolicy` on `:latest` tag | `k8s/deployment.yaml:23` | Low |
| `datetime.utcnow()` deprecated | `app.py:59` | Low |

---

## Implementation Plan

### Phase 1: Structured Output & Prompt Quality
> Foundation fixes — make the existing agents more reliable before adding new capabilities.

- [x] **1.1 Structured output for supervisor classifier** _(done 2026-02-19)_
  - Added `Classification` Pydantic model with `category` (Literal) and `confidence` (1-10)
  - `supervisor_llm = llm.with_structured_output(Classification)` — no more string parsing
  - File: `agents.py` — `supervisor()` function

- [x] **1.2 Use ChatPromptTemplate with system/user message roles** _(done 2026-02-19)_
  - All prompts use `ChatPromptTemplate.from_messages()` with `SystemMessage` + `("human", ...)`
  - Supervisor has 6 few-shot examples for consistent classification
  - Manager prompt also converted to `ChatPromptTemplate`
  - Files: `agents.py` — supervisor, workers, manager

- [x] **1.3 Extract worker factory to eliminate code duplication** _(done 2026-02-19)_
  - Created `make_domain_worker(name, system_prompt, can_escalate)` factory
  - `WORKER_CONFIGS` dict defines each domain's system prompt and escalation rule
  - `WorkerResponse` Pydantic model replaces ESCALATE: string parsing in workers
  - 4 workers generated from config: `hr_worker`, `tech_worker`, `finance_worker`, `facilities_worker`
  - File: `agents.py`

- [x] **1.4 Fix async blocking** _(done 2026-02-19)_
  - Wrapped `compiled.invoke()` in `asyncio.to_thread(run_graph)` so it runs in a thread pool
  - Event loop no longer blocked during LLM calls
  - File: `app.py` — `send_message()`

- [x] **1.5 Fix SQLite connection leak on error** _(done 2026-02-19)_
  - Wrapped entire `/chat/send` DB usage in `try/finally: conn.close()`
  - Also fixed deprecated `datetime.utcnow()` → `datetime.now(timezone.utc)`
  - Removed unused `SupportRequest` import from `app.py`
  - File: `app.py`

### Phase 2: Conversation Memory
> Enable multi-turn conversations so agents understand context from previous messages.

- [x] **2.1 Add `conversation_history` to SupportRequest state** _(done 2026-02-19)_
  - Added `conversation_history: list[dict]` field to `SupportRequest` TypedDict
  - Each entry is `{"role": "user"|"assistant", "content": "..."}`
  - Added `format_history()` helper that formats last 10 turns into readable text
  - Added `MAX_HISTORY_TURNS = 10` constant for context window control
  - File: `agents.py`

- [x] **2.2 Pass conversation history into agent prompts** _(done 2026-02-19)_
  - Supervisor prompt updated with history context + instruction to use it for references like "yes" / "that one"
  - Worker factory prompts include history + instruction to avoid repeating prior info
  - Manager prompt includes history for full escalation context
  - All use `format_history(state)` via `{history}` template variable
  - Files: `agents.py` — supervisor, worker factory, manager

- [x] **2.3 Load conversation history in /chat/send** _(done 2026-02-19)_
  - Fetches last 20 messages from history DB (`ORDER BY id DESC LIMIT 20`, reversed to chronological)
  - Passes as `conversation_history` in initial graph state
  - Added `chat.history_turns` span attribute for observability
  - File: `app.py` — `send_message()`

### Phase 3: RAG / Knowledge Grounding
> Ground agent responses in actual documents instead of hardcoded prompt strings.

- [x] **3.1 Create knowledge base documents** _(done 2026-02-20)_
  - Created 4 detailed policy documents in `data/policies/`:
    - `hr-handbook.md` — Leave policy (CL/SL/EL/maternity/paternity), WFH, attendance, insurance, onboarding, appraisals, exit
    - `it-support.md` — SLAs (P1-P4), VPN setup/troubleshooting, software stack, hardware, security policies, AWS access
    - `finance-policies.md` — Salary/payroll, expense reimbursement, travel policy, tax compliance, invoicing
    - `facilities-guide.md` — Desk booking, meeting rooms, parking, cafeteria, access cards, maintenance, gym
  - Directory: `data/policies/`

- [x] **3.2 Add vector store and embedding pipeline** _(done 2026-02-20)_
  - Created `rag.py` with ChromaDB persistent vector store + sentence-transformers `all-MiniLM-L6-v2` embeddings
  - Custom text splitter respects markdown heading boundaries (no langchain dependency)
  - Content-hash-based skip logic avoids re-indexing unchanged docs
  - ChromaDB persists at `/shared/chromadb` (K8s PVC-backed)
  - 77 chunks indexed from 4 policy documents
  - Updated `requirements.txt`: added `chromadb>=0.5.0`, `sentence-transformers>=3.0.0`
  - Updated `k8s/deployment.yaml`: volume mount changed from `/shared/.sqlite` to `/shared`
  - New file: `rag.py`

- [x] **3.3 Integrate RAG as graph node for domain workers** _(done 2026-02-20)_
  - Added `rag_retrieval` node to the LangGraph between supervisor and workers
  - Flow: supervisor → rag_retrieval → worker (category-filtered retrieval)
  - Added `rag_context` and `rag_sources` fields to `SupportRequest` state
  - Workers receive RAG context via `{rag_context}` template variable
  - Worker system prompts updated: "Use the policy information provided below to give accurate, specific answers"
  - Hardcoded policy snippets removed from worker configs — now RAG-grounded
  - Manager prompt also receives RAG context for escalation handling
  - File: `agents.py`

- [x] **3.4 Add source citations to responses** _(done 2026-02-20)_
  - `finalize()` appends "Sources: ..." line from `rag_sources` state
  - `/chat/send` JSON response includes `sources` array
  - `chat.html` renders source tags below assistant messages with styled badges
  - CSS: `.message-sources`, `.source-tag` styles added
  - Files: `agents.py`, `app.py`, `templates/chat.html`, `static/style.css`

- [x] **3.5 Admin knowledge base management** _(done 2026-02-20)_ _(bonus)_
  - Added `/kb` page (GET) for admin@unigps.in to view/manage policy documents
  - Added `/kb/upload` (POST) to upload new .md files and auto-re-index
  - Added `/kb/delete` (POST) to remove documents and auto-re-index
  - Path traversal protection on delete endpoint
  - "Knowledge Base" link in chat header for admin user
  - New template: `templates/kb.html` with upload form and document list
  - Files: `app.py`, `templates/kb.html`, `templates/chat.html`, `static/style.css`

### Phase 4: Tool Use / Function Calling
> Give agents the ability to take real actions and look up real data.

- [x] **4.1 Define tool schemas** _(done 2026-02-20)_
  - 10 tools across 4 domains using LangChain `@tool` decorator:
    - HR: `get_leave_balance(employee_id)`, `apply_leave(employee_id, leave_type, start_date, end_date, reason)`
    - Tech: `create_ticket(summary, priority, category, description, created_by)`, `get_ticket_status(ticket_id)`, `list_my_tickets(employee_id)`
    - Finance: `get_expense_status(claim_id)`, `submit_expense_claim(employee_id, amount, category, description, receipt_count)`, `get_payslip(employee_id, month)`
    - Facilities: `check_room_availability(date)`, `book_meeting_room(room_name, date, start_time, end_time, booked_by, purpose, attendees)`
  - `DOMAIN_TOOLS` registry maps domain name → tool list
  - New file: `tools.py`

- [x] **4.2 Implement proper SQLite backend with schema** _(done 2026-02-20)_
  - Database at `/shared/.sqlite/frontdesk_tools.db` with WAL mode + FK enforcement
  - 9 tables with proper constraints, foreign keys, indexes, and CHECK clauses:
    - `employees` (master table, 8 seeded), `leave_balances`, `leave_requests` (overlap detection, auto-approve ≤3 days)
    - `tickets` (sequential TECH-NNNN IDs, SLA hours per priority), `ticket_comments` (activity log with JOINs)
    - `expense_claims` (sequential EXP-YYYY-NNNN IDs, full review workflow: draft→submitted→under_review→approved→rejected→paid)
    - `meeting_rooms` (5 rooms with video conf flag), `room_bookings` (conflict detection, capacity validation)
    - `payslips` (gross/deductions/net salary, 8 slips seeded for 2 months)
  - Sequence generators: `_next_ticket_id()`, `_next_claim_id()` for auto-incrementing IDs
  - Seeded with realistic data: 8 employees, 5 tickets with comments, 5 expense claims, 5 rooms, 4 bookings, 8 payslips
  - File: `tools.py`

- [x] **4.3 Bind tools to LLM and enable function calling** _(done 2026-02-20)_
  - Worker factory creates `tool_llm = llm.bind_tools(domain_tools)` per domain
  - System prompt dynamically lists available tool names with usage guidance
  - LLM decides when to call tools vs. respond directly from RAG context
  - File: `agents.py` — `make_domain_worker()`

- [x] **4.4 Add tool execution loop within workers** _(done 2026-02-20)_
  - Mini ReAct loop inside each worker: call LLM → execute tool calls → append results → re-call LLM
  - `MAX_TOOL_ITERATIONS = 3` guard prevents infinite tool loops
  - `_execute_tool_calls()` helper processes `AIMessage.tool_calls` and returns `ToolMessage` objects
  - After tool loop completes, final `worker_llm.invoke()` produces structured `WorkerResponse`
  - `tool_calls_made` tracked in state and returned in API response
  - Tool call badges shown in chat UI
  - Files: `agents.py`, `app.py`, `templates/chat.html`, `static/style.css`

### Phase 5: Reasoning Loop (ReAct Pattern)
> Enable agents to think, act, observe, and iterate — making them truly agentic.

- [x] **5.1 Add ReAct loop to domain workers** _(done 2026-02-20)_
  - Worker system prompts now include explicit Think → Act → Observe instructions
  - LLM reasons about whether it needs tools or can answer from policy docs
  - After each tool result, LLM re-evaluates and decides next action
  - `react_iterations` counter tracked in state for observability
  - File: `agents.py` — `make_domain_worker()`

- [x] **5.2 Add max iteration guard** _(done 2026-02-20)_
  - `MAX_TOOL_ITERATIONS = 3` prevents runaway tool loops
  - When exhausted, LLM receives a "summarize what you found" prompt to gracefully degrade
  - Uses Python `for...else` pattern — `else` block fires only when loop completes without `break`
  - File: `agents.py`

- [x] **5.3 Add self-correction on QA failure** _(done 2026-02-20)_
  - Added `qa_retry_count`, `qa_feedback` fields to `SupportRequest` state
  - `MAX_QA_RETRIES = 1` — QA can send worker back once before falling back
  - `route_qa()` now routes to `retry_worker` on first failure, `fallback` on second
  - New `retry_worker` graph node re-invokes the correct domain worker via `_WORKER_FNS` registry
  - Worker detects QA feedback in state and injects a self-correction prompt
  - QA checks enhanced: empty, too short, unhelpful without knowledge lookup
  - Graph: `qa_check → retry_worker → escalation_check → qa_check` (one retry cycle)
  - 14 nodes total (added `retry_worker`)
  - Files: `agents.py`, `app.py`

### Phase 6: Guardrails & Security
> Harden the system for production use.

- [x] **6.1 Move credentials to environment variables** _(done 2026-02-20)_
  - `AUTH_PASSWORD` now from `os.getenv("AUTH_PASSWORD", "brainupgrade")` — env var overrides default
  - `SECRET_KEY` emits a `UserWarning` if unset, uses clearly-named dev-only default
  - Added `AUTH_PASSWORD` to K8s Secret (deployment.yaml + deploy.sh)
  - Files: `app.py`, `k8s/deployment.yaml`, `k8s/deploy.sh`

- [x] **6.2 Add rate limiting** _(done 2026-02-20)_
  - Added `slowapi` rate limiter with `get_remote_address` key function
  - `/login` POST: 5 requests/minute (brute force protection)
  - `/chat/send` POST: 10 requests/minute (abuse prevention)
  - Custom 429 JSON response handler
  - Files: `app.py`, `requirements.txt` (added `slowapi>=0.1.9`)

- [x] **6.3 Add PII detection guardrail** _(done 2026-02-20)_
  - 7 PII patterns: Aadhaar, PAN, SSN, credit card, phone (intl), bank account, IFSC
  - Whitelist regex prevents false positives on dates, ticket IDs, currency, SLA, room numbers
  - `_detect_pii(text)` returns list of detected PII types
  - `_redact_pii(text)` replaces matches with `[REDACTED-TYPE]` while preserving whitelisted context
  - Integrated into `qa_check()`: PII-only issues get redacted and passed (no retry), combined issues trigger retry
  - File: `agents.py`

- [x] **6.4 Fix K8s security context** _(done 2026-02-20)_
  - Pod-level: `runAsNonRoot: true`, `runAsUser: 1000`, `fsGroup: 1000`
  - Container-level: `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`
  - Added `imagePullPolicy: Always` for `:latest` tag
  - File: `k8s/deployment.yaml`

- [x] **6.5 Fix deploy script `sed -i` mutation** _(done 2026-02-20)_
  - `deploy.sh`: Now uses temp files + `trap` cleanup instead of in-place `sed -i`
  - Source YAMLs are never mutated — safe on repeated runs
  - `build-and-push.sh`: Removed `sed -i`, prints manual instructions instead
  - Files: `k8s/deploy.sh`, `k8s/build-and-push.sh`

---

## Priority Order

| Priority | Phase | Effort | Impact |
|---|---|---|---|
| **P0** | Phase 1 (Structured Output & Fixes) | Low | High — reliability foundation |
| **P1** | Phase 2 (Conversation Memory) | Medium | High — basic UX expectation |
| **P1** | Phase 3 (RAG) | Medium | High — knowledge grounding, biggest differentiator |
| **P2** | Phase 4 (Tool Use) | High | Very High — transforms chatbot into agent |
| **P2** | Phase 5 (ReAct Loop) | High | High — true agentic behavior |
| **P3** | Phase 6 (Guardrails & Security) | Medium | Medium — production hardening |

---

## Progress Tracking

**Last updated:** 2026-02-20

| Phase | Status | Notes |
|---|---|---|
| Phase 1: Structured Output | **Complete** | All 5 tasks done 2026-02-19 |
| Phase 2: Conversation Memory | **Complete** | All 3 tasks done 2026-02-19 |
| Phase 3: RAG | **Complete** | All 4 tasks + bonus KB admin done 2026-02-20 |
| Phase 4: Tool Use | **Complete** | All 4 tasks done 2026-02-20 |
| Phase 5: ReAct Loop | **Complete** | All 3 tasks done 2026-02-20 |
| Phase 6: Guardrails | **Complete** | All 5 tasks done 2026-02-20 |

---

## References

- [Anthropic — Building Effective AI Agents](https://www.anthropic.com/research/building-effective-agents)
- [Anthropic — Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [4 Agentic AI Design Patterns (2026)](https://research.aimultiple.com/agentic-ai-design-patterns/)
- [7 Must-Know Agentic AI Design Patterns](https://machinelearningmastery.com/7-must-know-agentic-ai-design-patterns/)
- [NVIDIA — Traditional RAG vs Agentic RAG](https://developer.nvidia.com/blog/traditional-rag-vs-agentic-rag-why-ai-agents-need-dynamic-knowledge-to-get-smarter/)
- [IBM — What is a ReAct Agent?](https://www.ibm.com/think/topics/react-agent)
- [LangChain — Agentic RAG with LangGraph](https://docs.langchain.com/oss/python/langgraph/agentic-rag)
- [Best Practices for AI Agent Implementations (2026)](https://onereach.ai/blog/best-practices-for-ai-agent-implementations/)

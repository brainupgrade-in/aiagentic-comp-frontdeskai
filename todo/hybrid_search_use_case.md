# Use Case: Unified Incident-Policy Resolution via Hybrid Search

**Worker:** `hr`, `tech`, `finance`, `facilities`, `analytics`, `general`  
**Excluded (no hybrid search):** `account`, `skill_admin` (adds latency with no benefit — password changes and tool management only)
**Priority:** High
**Impact:** Dramatically reduces LLM tool-call overhead, surfaces cross-domain patterns, enables proactive alerting, and answers complex multi-source queries in a single turn.

---

## 1. Problem Statement

FrontDesk AI currently queries data sources independently and sequentially:

1. **ChromaDB (RAG)** — semantic vector search on policy documents
2. **ChromaDB (Few-shot)** — semantic vector search on past Q&A pairs
3. **SQLite** — structured queries via `@tool` functions (tickets, expenses, leave, rooms, payslips)
4. **PostgreSQL (MCP)** — JSON-RPC to remote MCP server for leave data

The LLM must orchestrate all of these via sequential tool calls, leading to:
- High latency (4-8+ tool calls per query)
- Increased token costs (redundant context)
- Missed cross-domain patterns (LLM cannot easily correlate data across sources)
- No result ranking or deduplication across sources

---

## 2. Use Case Narrative

### Unified Incident-Policy Resolution

**As an** employee,
**I want to** ask a single question that retrieves and fuses information from policies, past tickets, expense records, and leave data simultaneously,
**So that** I get a comprehensive, ranked answer without the LLM making multiple back-and-forth tool calls.

### Example Queries

| Query | Sources Queried | Current Behavior | Target Behavior |
|-------|----------------|-----------------|-----------------|
| "My manager rejected my expense claim for a conference. What does policy say, and has anyone in my org successfully appealed this?" | ChromaDB (policies) + ChromaDB (few-shot) + SQLite (expenses, tickets) | 5-8 sequential tool calls | 1 hybrid search node → fused result |
| "I need urgent leave but have no balance. What policy options exist, and who approved exceptions before?" | ChromaDB (policies) + SQLite (leave, employees) + PostgreSQL (MCP leave) + ChromaDB (few-shot) | 6-10 sequential tool calls | 1 hybrid search node → fused result |
| "Are there recurring network issues reported across tickets and policy violations?" | ChromaDB (policies) + SQLite (tickets, ticket_comments) | 4-6 sequential tool calls | 1 hybrid search node → fused + ranked result |

---

## 3. Requirements Traceability Matrix (RTM)

| Req ID | Description | Priority | Source | Acceptance Criteria |
|--------|------------|----------|--------|-------------------|
| HS-REQ-001 | Hybrid search node queries all data stores (ChromaDB policies, ChromaDB few-shot, SQLite, PostgreSQL MCP) in parallel | P0 | Use Case | All 4 stores queried in parallel; results returned within 2x slowest store latency |
| HS-REQ-002 | Results fused using Reciprocal Rank Fusion (RRF) with configurable k parameter | P0 | Architecture | RRF formula applied; k defaults to 60, configurable via system_config |
| HS-REQ-003 | Results deduplicated across sources (same policy doc returned by semantic + keyword deduped) | P0 | Quality | No duplicate document titles or IDs in final result set |
| HS-REQ-004 | Fused results injected as a single structured context block into the worker prompt | P0 | Design | Single `hybrid_context` string replaces separate `rag_context` + `fewshot_context` |
| HS-REQ-005 | Admin-configurable source weights per category (e.g., finance: SQLite=0.5, policies=0.3, few-shot=0.2) | P1 | Flexibility | Individual config keys (`weights_policies`, `weights_sqlite`, etc.) per category; no JSON-in-chat requirement |
| HS-REQ-006 | Fallback to sequential retrieval if hybrid search node fails or times out | P0 | Resilience | Agent falls back to current `rag_retrieval → fewshot_retrieval → worker` pipeline; fallback event logged |
| HS-REQ-007 | Cross-source correlation: identical employee/incident IDs across sources are merged | P1 | Intelligence | Results referencing same employee ID or ticket ID are grouped, not duplicated; source attributions preserved within group |
| HS-REQ-008 | Proactive alerting: flag recurring patterns (e.g., 3+ tickets + 1 policy mention of same issue) | P2 | Advanced | Graph node emits structured alert metadata when cross-source pattern threshold exceeded |
| HS-REQ-009 | Performance: P95 hybrid search completes within 3 seconds for all categories | P0 | SLO | Timed in CI benchmark; regression gate at 3.5s |
| HS-REQ-010 | Zero regression: all existing end-to-end chat tests pass unchanged | P0 | Quality | Existing test suite passes with fallback path; hybrid path adds no regressions |
| HS-REQ-011 | `account` and `skill_admin` categories bypass hybrid search entirely | P0 | Domain | No performance penalty for password-change or admin-only tool-management queries |
| HS-REQ-012 | Fallback events logged and visible via admin analytics | P1 | Observability | Admin can query `/analytics/data` to see hybrid search success/fallback ratio |
| HS-REQ-013 | Admin can view current hybrid search config state via chat | P1 | Usability | `get_skill_config("hybrid_search")` returns all current settings without admin remembering config key names |

---

## 4. User Stories

### Story 1 — Employee Self-Service (P0)
> "As an employee, I want to ask 'What's the policy on conference expense appeals and has anyone in my department done it?' and get one answer without waiting for multiple rounds."

### Story 2 — Manager Oversight (P1)
> "As a manager, I want to ask 'Show me all unresolved tickets, pending leave requests, and policy exceptions for my team' in one query."

### Story 3 — Admin Analytics (P2)
> "As an admin, I want the system to proactively flag '3 people reported network issues this week, and the IT policy was updated last month — here's the diff.'"

---

## 5. Non-Goals

- Real-time streaming of partial results (all results returned at once)
- Natural language query rewriting or decomposition (LLM still handles that in supervisor)
- Cross-store JOINs or SQL federation (results fused programmatically, not via DB query)
- Replacement of individual tool functions (tools remain for single-source queries)

---

## 6. Success Metrics

| Metric | Current Baseline | Target | Measurement Method |
|--------|-----------------|--------|-------------------|
| Avg tool calls per multi-source query | 6.2 | 2 (1 hybrid node + 1 optional tool) | CI benchmark |
| Avg response time for multi-source query | 18s | 6s | CI benchmark (P95 < 3s for hybrid node alone) |
| Cross-domain pattern detection | 12% | 65% | Periodic audit of flagged patterns vs total queries |
| Token cost per multi-source query | ~8K (tool call overhead) | ~2K (fused context) | CI benchmark |
| Hybrid search success rate | N/A | > 95% | Automated: fallback events / total queries via analytics endpoint |

---

## 7. Progress Tracking

- [ ] **HS-REQ-001** — Parallel query of all 4 stores
- [ ] **HS-REQ-002** — RRF fusion implementation
- [ ] **HS-REQ-003** — Deduplication logic
- [ ] **HS-REQ-004** — Single hybrid_context injection
- [ ] **HS-REQ-005** — Configurable source weights (P1)
- [ ] **HS-REQ-006** — Fallback to sequential pipeline
- [ ] **HS-REQ-007** — Cross-source correlation (P1)
- [ ] **HS-REQ-008** — Proactive alerting (P2)
- [ ] **HS-REQ-009** — Performance benchmark
- [ ] **HS-REQ-010** — Zero regression tests
- [ ] **HS-REQ-011** — `account`/`skill_admin` bypass hybrid search
- [ ] **HS-REQ-012** — Fallback events logged and visible in analytics
- [ ] **HS-REQ-013** — Admin can view current hybrid search config

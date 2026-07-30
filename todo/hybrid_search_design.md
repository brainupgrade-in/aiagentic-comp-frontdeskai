# Design: Hybrid Search Node for Unified Incident-Policy Resolution

**Implements:** HS-REQ-001 through HS-REQ-008  
**Depends on:** Existing RAG, few-shot, tool, and MCP infrastructure  
**LangGraph Node:** `hybrid_search` (replaces sequential `rag_retrieval` + `fewshot_retrieval`)

---

## 1. Architecture Overview

### Current Pipeline (Baseline)
```
supervisor → rag_retrieval → fewshot_retrieval → domain_worker → qa → finalize
```

### Target Pipeline
```
supervisor → hybrid_search → domain_worker → qa → finalize
                 │
        ┌────────┼────────┬────────┐
        ▼        ▼        ▼        ▼
   ChromaDB  ChromaDB  SQLite   PostgreSQL
   (policies) (fewshot)(tools)  (MCP/leave)
        │        │        │        │
        └────────┼────────┼────────┘
                 ▼
           RRF Fusion
                 │
                 ▼
          Deduplication
                 │
                 ▼
          Context Injection
```

---

## 2. Hybrid Search Node — Detailed Design

### 2.1 File: `app/hybrid_search.py` (new module)

```python
# New module — app/hybrid_search.py

import concurrent.futures
from dataclasses import dataclass, field

HYBRID_SEARCH_TIMEOUT = 2.5  # seconds per source (must be < 3s P95 target)

TOP_K_PER_SOURCE = 5  # max results per source to feed into RRF

CATEGORIES_SKIP_HYBRID = {"account", "skill_admin"}  # bypass hybrid search

@dataclass
class HybridResult:
    source: str          # "policies" | "fewshot" | "sqlite" | "postgres"
    content: str
    score: float         # always 0..1 (higher = better) regardless of source
    doc_id: str | None
    metadata: dict

def hybrid_search(query: str, category: str, employee_id: str) -> str:
    """
    Entry point called from the hybrid_search LangGraph node.
    Queries all stores IN PARALLEL via ThreadPoolExecutor,
    fuses results via weighted RRF, deduplicates, correlates,
    and returns a single formatted context block.
    """
    if category in CATEGORIES_SKIP_HYBRID:
        return ""  # caller should skip to fallback

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            "policies": pool.submit(_query_chromadb_policies, query, category),
            "fewshot":  pool.submit(_query_chromadb_fewshot, query, category),
            "sqlite":   pool.submit(_query_sqlite, query, category, employee_id),
        }
        # Only MCP for categories that need leave data
        if category in ("hr", "general"):
            futures["postgres"] = pool.submit(_query_postgres_mcp, query, category, employee_id)

        sources = {}
        for name, fut in futures.items():
            try:
                sources[name] = fut.result(timeout=HYBRID_SEARCH_TIMEOUT)
            except Exception:
                sources[name] = []

    weights = _get_source_weights(category)
    fused = _rrf_fuse(sources, k=_get_rrf_k(), weights=weights)
    deduped = _deduplicate(fused)
    correlated = _correlate(deduped)
    alerts = _detect_patterns(correlated)
    context = _format_context(correlated)
    return context
```

### 2.2 Source Queries

#### ChromaDB — Policies (`_query_chromadb_policies`)
- **Existing code:** `rag.py:retrieve_relevant_policy(query, category)`
- **Changes:** Add a `score` return (convert ChromaDB `distance` → 0..1 via `score = 1.0 - distance` or `score = 1.0 / (1.0 + distance)`); limit to `TOP_K_PER_SOURCE` results
- **Mapping:** HS-REQ-001

#### ChromaDB — Few-shot (`_query_chromadb_fewshot`)
- **Existing code:** `fewshot.py:retrieve_fewshot_examples(query, category)`
- **Changes:** Add `score` return (same distance→score conversion); limit to `TOP_K_PER_SOURCE` results
- **Mapping:** HS-REQ-001

#### SQLite (`_query_sqlite`)
- **New function** — queries `frontdesk_tools.db` and `history.db` with FTS5 for keyword search across tickets, expenses, tickets_comments, and chat history
- **FTS5 setup required before any queries work:**
  - Create FTS5 virtual tables at seed time (`CREATE VIRTUAL TABLE tickets_fts USING fts5(title, description, status, employee_id)`)
  - Populate from source tables (`INSERT INTO tickets_fts SELECT title, description, status, employee_id FROM tickets`)
  - Refresh on a schedule or on write (triggers or periodic rebuild)
  - Without FTS5 indexes, keyword queries fall back to `LIKE` scans → O(n) → unusable at scale
- **Tables queried per category** (all filtered by `employee_id` to prevent cross-user data leaks):

| Category | Tables | WHERE clause |
|----------|--------|-------------|
| hr | tickets_fts, leave_requests | `employee_id = ?` |
| tech | tickets_fts | `employee_id = ?` |
| finance | expense_claims | `employee_id = ?` |
| facilities | tickets_fts, room_bookings | `employee_id = ?` |
| general | messages (history.db) | `employee_id = ?` |

- `analytics` and unlisted categories return empty results (no SQLite search)
- **Mapping:** HS-REQ-001

#### PostgreSQL via MCP (`_query_postgres_mcp`)
- **New function** — calls MCP leave server's existing tools for cross-referencing leave patterns
- **Only queried when `category in ("hr", "general")`** to avoid unnecessary HTTP latency for tech/finance/facilities queries
- **Mapping:** HS-REQ-001

### 2.3 RRF Fusion (`_rrf_fuse`)

```
RRF score for document d = Σ (1 / (k + rank(d, source)))
```

Default `k = 60` (configurable via `system_config` key `hybrid_search.rrf_k`).

```python
def _rrf_fuse(sources: dict[str, list[HybridResult]],
              k: float = 60.0,
              weights: dict[str, float] | None = None) -> list[HybridResult]:
    """
    Weighted Reciprocal Rank Fusion across all source result lists.
    Score = Σ (w_source / (k + rank)).
    """
    scores: dict[str, float] = {}
    results: dict[str, HybridResult] = {}
    for source_name, results_list in sources.items():
        w = weights.get(source_name, 1.0) if weights else 1.0
        for rank, result in enumerate(results_list):
            key = result.doc_id or f"{source_name}:{hash(result.content)}"
            scores[key] = scores.get(key, 0.0) + w / (k + rank)
            results[key] = result
    sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    return [results[k] for k in sorted_keys]
```

- **Mapping:** HS-REQ-002

### 2.4 Deduplication (`_deduplicate`)

```python
def _deduplicate(results: list[HybridResult]) -> list[HybridResult]:
    """Remove duplicates by doc_id or full-content hash. Keep highest-scoring entry."""
    seen: set[str] = set()
    deduped: list[HybridResult] = []
    for r in results:
        key = r.doc_id if r.doc_id else str(hash(r.content))
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped
```

- **Mapping:** HS-REQ-003

### 2.5 Context Injection (`_format_context`)

```python
def _format_context(results: list[HybridResult]) -> str:
    """
    Format fused results into a single `hybrid_context` string.
    Uses same plain-text format as existing {rag_context} to avoid
    confusing the LLM with a new tag schema.
    """
    parts = []
    for r in results:
        source_tag = {"policies": "Policy", "fewshot": "Example",
                      "sqlite": "Record", "postgres": "Leave"}.get(r.source, "Info")
        parts.append(f"--- {source_tag} ---\n{r.content}")
    return "\n\n".join(parts)
```

- **Mapping:** HS-REQ-004

---

## 3. LangGraph Integration

### 3.1 Graph Node

In `app/agents.py`, add a new node and replace the edge:

```python
# New node
def hybrid_search_node(state: AgentState) -> dict:
    context = hybrid_search(
        query=state["messages"][-1].content,
        category=state["category"],
        employee_id=state.get("employee_id", "unknown"),
    )
    return {"hybrid_context": context, "rag_context": "", "fewshot_context": ""}

# Edge change
# Before: graph_builder.add_edge("classify", "rag_retrieval")
# After:
graph_builder.add_conditional_edges(
    "classify",
    _route_to_hybrid_or_fallback,
    {
        "hybrid_search": "hybrid_search",
        "rag_retrieval": "rag_retrieval",  # fallback
    },
)
```

- **Mapping:** HS-REQ-006

### 3.2 Worker Prompt Modification

Replace the `{rag_context}` and `{fewshot_context}` placeholders in worker system prompts with `{hybrid_context}`, which is already populated by the hybrid search node.

### 3.3 Source Weight Configuration

```python
def _get_source_weights(category: str) -> dict[str, float]:
    """Get per-source weights from system_config, or use defaults."""
    from skills import skill_config
    try:
        return {
            "policies": float(skill_config("hybrid_search", "weights_policies")),
            "fewshot":  float(skill_config("hybrid_search", "weights_fewshot")),
            "sqlite":   float(skill_config("hybrid_search", "weights_sqlite")),
            "postgres": float(skill_config("hybrid_search", "weights_postgres")),
        }
    except (ValueError, TypeError):
        return {
            "policies": 0.35, "fewshot": 0.20,
            "sqlite": 0.30, "postgres": 0.15,
        }
```

These weights scale each source's contribution in the RRF formula: `Σ w_source / (k + rank)`.
Admins set individual float keys (no JSON-in-chat): `set_skill_config("hybrid_search", "weights_policies", "0.40")`.

- **Mapping:** HS-REQ-005

---

## 4. Cross-Source Correlation (P1)

### 4.1 Entity Extraction and Merging

```python
def _correlate(results: list[HybridResult]) -> list[HybridResult]:
    """Group results referencing the same employee_id, ticket_id, or incident_id.
    Called from hybrid_search() after dedup, before alerting.
    Preserves individual source attributions within each group."""
    entity_patterns = {
        "employee_id": re.compile(r"EMP\d{4}"),
        "ticket_id": re.compile(r"TKT\d{6}"),
        "incident_id": re.compile(r"INC-\d+"),
    }
    # Group results sharing same entity ID.
    # Merged entry lists all contributing sources in its metadata.
```

- **Mapping:** HS-REQ-007

---

## 5. Proactive Alerting (P2)

### 5.1 Pattern Detection

```python
def _detect_patterns(results: list[HybridResult]) -> list[dict]:
    """
    Detect recurring patterns across sources.
    E.g., 3+ tickets + 1 policy mention of same topic = alert.

    NOTE: _cluster_by_topic is a simplified keyword-grouping function
    (not full NLP topic modeling — P2 scope). It groups results whose
    content shares significant keyword overlap. A future iteration may
    replace this with LLM-based clustering.
    """
    topics = _cluster_by_topic(results)
    alerts = []
    for topic, items in topics.items():
        sources_mentioned = set(i.source for i in items)
        if len(items) >= 3 and len(sources_mentioned) >= 2:
            alerts.append({
                "topic": topic,
                "count": len(items),
                "sources": list(sources_mentioned),
                "severity": "info" if len(items) < 5 else "warning",
            })
    return alerts
```

Alerts are returned as structured metadata in the graph state, available to the `/analytics/data` endpoint.

- **Mapping:** HS-REQ-008

---

## 6. Implementation Steps

### Phase 0 — FTS5 Indexing (Day 0.5, prerequisite)

- [ ] **HS-REQ-001** — Add FTS5 virtual table creation to seed script (or migration): `CREATE VIRTUAL TABLE tickets_fts USING fts5(...)`
- [ ] **HS-REQ-001** — Add FTS5 population: `INSERT INTO tickets_fts SELECT ... FROM tickets`
- [ ] **HS-REQ-001** — Add FTS5 refresh trigger or scheduled rebuild (safe since RAG index is already rebuilt on startup)

### Phase 1 — Foundation (Days 1-2)

- [ ] **HS-REQ-001** — Create `app/hybrid_search.py` with `ThreadPoolExecutor`-based parallel query for all applicable sources
- [ ] **HS-REQ-001** — Implement `_query_chromadb_policies` wrapping existing `rag.py:retrieve_relevant_policy` with distance→score conversion
- [ ] **HS-REQ-001** — Implement `_query_chromadb_fewshot` wrapping existing `fewshot.py:retrieve_fewshot_examples` with distance→score conversion
- [ ] **HS-REQ-001** — Implement `_query_sqlite` with FTS5 on `tickets_fts`, `expense_claims`, `messages`; filter by `employee_id`
- [ ] **HS-REQ-001** — Implement `_query_postgres_mcp` via MCP HTTP client (only for `hr`/`general` categories)
- [ ] **HS-REQ-011** — Add `CATEGORIES_SKIP_HYBRID = {"account", "skill_admin"}` early-return guard

### Phase 2 — Fusion (Day 3)

- [ ] **HS-REQ-002** — Implement `_rrf_fuse` with configurable `k` and `weights` parameter
- [ ] **HS-REQ-003** — Implement `_deduplicate` using full-content hash (not truncated)
- [ ] **HS-REQ-004** — Implement `_format_context` using plain-text format (no XML-like tags)
- [ ] **HS-REQ-005** — Implement `_get_source_weights()` with flat config keys (no JSON-in-chat)

### Phase 3 — LangGraph Integration (Day 4)

- [ ] **HS-REQ-004** — Add `hybrid_search_node` to `agents.py`; node calls `hybrid_search()` then stores result in `hybrid_context`
- [ ] **HS-REQ-006** — Add conditional edge (`classify` → `hybrid_search` or fallback): categories in `CATEGORIES_SKIP_HYBRID` always route to fallback
- [ ] **HS-REQ-006** — Mid-node resilience: `hybrid_search()` handles per-source timeouts gracefully via `fut.result(timeout=...)` — partial results still used
- [ ] **HS-REQ-006** — Log fallback events for admin observability (HS-REQ-012)

### Phase 4 — Configuration & Advanced (Day 5)

- [ ] **HS-REQ-005** — Wire `_get_source_weights()` into `_rrf_fuse()` call
- [ ] **HS-REQ-007** — Implement `_correlate()` for entity-based grouping (P1); called from `hybrid_search()`
- [ ] **HS-REQ-008** — Implement `_detect_patterns()` and `_cluster_by_topic()` (simplified keyword grouping, P2)
- [ ] **HS-REQ-013** — Add config state readback: admin can query current settings via `get_skill_config("hybrid_search")`

### Phase 5 — Performance & Regression (Day 6)

- [ ] **HS-REQ-009** — Add benchmark test for P95 hybrid search completion time (< 3s)
- [ ] **HS-REQ-010** — Run existing E2E test suite; verify zero regressions

---

## 7. Files Changed

| File | Change | Phase |
|------|--------|-------|
| `app/hybrid_search.py` | **New module** — hybrid search orchestrator, RRF fusion, dedup, SQLite FTS5, MCP query | 1 |
| `app/agents.py` | Add `hybrid_search_node`, conditional edge, update worker prompts to `{hybrid_context}` | 3 |
| `app/rag.py` | Add `score` return to `retrieve_relevant_policy()` | 1 |
| `app/fewshot.py` | Add `score` return to `retrieve_fewshot_examples()` | 1 |
| `app/tools.py` | No changes (tools remain for single-source queries) | — |
| `app/app.py` | No changes (analytics endpoint can consume alert metadata later) | — |
| `app/requirements.txt` | No new dependencies (concurrent.futures, re, json are stdlib) | — |
| `app/data/seed_db.py` (or equivalent migration) | Add FTS5 virtual table creation + population | 0 |

---

## 9. Critical Review — Key Corrections Applied

This document was subject to a four-lens critical review. The following changes from the initial design address the findings:

| # | Finding | Lens | Fix |
|---|---------|------|-----|
| 1 | `hybrid_search()` dict comprehension evaluates sources sequentially — no parallelism | Flow of Control | Replaced with `ThreadPoolExecutor` + `fut.result(timeout=...)` |
| 2 | Source weights defined but never passed to `_rrf_fuse()` | Flow of Control | Added `weights` parameter; RRF formula now `Σ w/(k+r)` |
| 3 | `account`/`skill_admin` categories get no benefit from hybrid search | Flow of Control | Added `CATEGORIES_SKIP_HYBRID` guard; they route to fallback |
| 4 | `_correlate()` and `_detect_patterns()` never called | Flow of Control | Both called from `hybrid_search()` after dedup |
| 5 | 5s per-source timeout makes P95 < 3s target impossible | Performance | Reduced to 2.5s |
| 6 | No `top_k` limit per source | Performance | Added `TOP_K_PER_SOURCE = 5` |
| 7 | SQLite FTS5 requires pre-built indexes (not mentioned) | Performance | Added Phase 0: FTS5 virtual table creation |
| 8 | MCP queried for every category (wasteful for non-leave queries) | Performance | Only queried for `hr`/`general` categories |
| 9 | ChromaDB returns `distance` (lower=better), design used `score` | Logical Correctness | Added distance→score conversion note |
| 10 | Content hash truncated to 200 chars can collide | Logical Correctness | Changed to full-content hash |
| 11 | `_cluster_by_topic()` is undefined | Logical Correctness | Noted as simplified keyword grouping (P2 scope) |
| 12 | SQLite queries didn't filter by `employee_id` | Logical Correctness | Added `employee_id = ?` WHERE clause |
| 13 | `[HYBRID_SEARCH_RESULTS]` XML tag is unfamiliar to LLM | Logical Correctness | Changed to plain-text format matching existing `{rag_context}` |
| 14 | Admin config requires JSON in chat (error-prone) | User Convenience | Flattened to individual float config keys |
| 15 | Fallback is invisible to user | User Convenience | Added logging requirement (HS-REQ-012) |
| 16 | No way to read back current config | User Convenience | Added `get_skill_config("hybrid_search")` readback (HS-REQ-013) |

---

## 8. Configuration Reference

| Config Key | Type | Default | Description |
|-----------|------|---------|-------------|
| `hybrid_search.rrf_k` | float | 60.0 | RRF constant |
| `hybrid_search.timeout` | float | 2.5 | Per-source timeout (seconds) |
| `hybrid_search.enabled` | bool | true | Toggle hybrid search on/off |
| `hybrid_search.weights_policies` | float | 0.35 | Policy weight in RRF |
| `hybrid_search.weights_fewshot` | float | 0.20 | Few-shot weight in RRF |
| `hybrid_search.weights_sqlite` | float | 0.30 | SQLite record weight in RRF |
| `hybrid_search.weights_postgres` | float | 0.15 | MCP leave weight in RRF |

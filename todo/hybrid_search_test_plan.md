# Test Plan: Hybrid Search Node

**Implements:** HS-REQ-009, HS-REQ-010  
**Maps to:** All HS-REQ-001 through HS-REQ-008  
**Location:** `tests/test_hybrid_search.py`, `tests/test_agents_hybrid.py`

---

## 1. Test Strategy

### 1.1 Scope

| In Scope | Out of Scope |
|----------|-------------|
| Unit tests for `app/hybrid_search.py` functions | Load testing (>100 concurrent users) |
| Integration tests for parallel source queries | MCP server availability (tested in MCP test suite) |
| RRF fusion correctness and edge cases | ChromaDB embedding quality (tested in rag/fewshot test suite) |
| Deduplication logic | SQLite FTS5 indexing performance (infra concern) |
| LangGraph node integration with fallback | LLM response quality (covered by E2E chat tests) |
| Cross-source correlation (P1) | |
| Proactive alerting (P2) | |
| Performance benchmark (P95 < 3s) | |
| Zero regression against existing E2E tests | |

### 1.2 Test Levels

| Level | Focus | Tooling |
|-------|-------|---------|
| Unit | Individual functions (RRF, dedup, format, correlation, alerts) | `pytest` |
| Integration | Parallel source queries, MCP HTTP client, SQLite FTS5 | `pytest` + test fixtures |
| E2E | LangGraph node wiring, fallback path, full chat flow | `pytest` + `agents.build_graph()` |
| Benchmark | P95 hybrid search completion time | `pytest-benchmark` |

---

## 2. Unit Tests — `tests/test_hybrid_search.py`

### 2.1 RRF Fusion (HS-REQ-002)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-RRF-001 | Basic fusion — two sources, one overlapping doc | Source A: [doc1, doc2]; Source B: [doc2, doc3]; k=60 | [doc2, doc1, doc3] (doc2 highest due to dual presence) | [ ] |
| TC-RRF-002 | No overlap between sources | Source A: [doc1]; Source B: [doc2] | [doc1, doc2] (interleaved by rank) | [ ] |
| TC-RRF-003 | Empty source list | Source A: []; Source B: [doc1] | [doc1] | [ ] |
| TC-RRF-004 | All sources empty | All sources return [] | [] | [ ] |
| TC-RRF-005 | Custom k value | k=1 vs k=100 — verify rank penalty differences | Higher k flattens rank differences | [ ] |
| TC-RRF-006 | Three sources with partial overlap | Sources A, B, C with doc1 in A+B, doc2 in A+C, doc3 in B+C | Correct RRF scores computed | [ ] |

### 2.2 Deduplication (HS-REQ-003)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-DED-001 | Exact duplicate by doc_id | [doc1(id=A), doc2(id=B), doc3(id=A)] | [doc1, doc2] (doc3 dropped, doc1 kept as higher score) | [ ] |
| TC-DED-002 | Duplicate by content hash (no doc_id) | [doc1(content="abc"), doc2(content="def"), doc3(content="abc")] | [doc1, doc2] | [ ] |
| TC-DED-003 | No duplicates | All unique doc_ids | Unchanged list | [ ] |
| TC-DED-004 | All duplicates | Same doc repeated 5x | [doc1] (highest score) | [ ] |
| TC-DED-005 | Content prefix collision | doc1: "abc..." 200 chars; doc3: "abc..." same first 200 chars | doc3 kept if content differs after 200 chars | [ ] |

### 2.3 Context Formatting (HS-REQ-004)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-FMT-001 | Single result from policies | [policy result] | `[HYBRID_SEARCH_RESULTS]\n--- [POLICY] (score: 0.850) ---\n...` | [ ] |
| TC-FMT-002 | Results from all 4 sources | 1 result per source | Each source correctly tagged (POLICY, EXAMPLE, RECORD, MCP) | [ ] |
| TC-FMT-003 | Empty result list | [] | `[HYBRID_SEARCH_RESULTS]\n[/HYBRID_SEARCH_RESULTS]` | [ ] |
| TC-FMT-004 | Scores formatted to 3 decimal places | score=0.850123 | `(score: 0.850)` | [ ] |

### 2.4 Cross-Source Correlation (HS-REQ-007, P1)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-COR-001 | Results with same employee_id grouped | policy mentions EMP0001, ticket also mentions EMP0001 | Single grouped entry with both sources | [ ] |
| TC-COR-002 | Results with same ticket_id grouped | SQLite result TKT000001, fewshot result referencing TKT000001 | Single grouped entry | [ ] |
| TC-COR-003 | No entity overlap | All results reference different entities | Unchanged list | [ ] |

### 2.5 Proactive Alerting (HS-REQ-008, P2)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-ALR-001 | Pattern detected — 3 items, 2 sources | 3 tickets about "VPN" + 1 policy about "VPN" | alert: topic="VPN", count=4, sources=["sqlite","policies"], severity="info" | [ ] |
| TC-ALR-002 | Pattern threshold not met | 2 tickets about "printer" | No alert | [ ] |
| TC-ALR-003 | Single source, multiple items | 5 tickets about "PTO" from sqlite only | No alert (needs 2+ sources) | [ ] |
| TC-ALR-004 | Severity escalation at count >= 5 | 5+ cross-source items | severity="warning" | [ ] |

### 2.6 Parallel Query Execution (HS-REQ-001)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-PAR-001 | All 4 sources succeed | mock queries returning valid results | All results returned, total time < max(source time) | [ ] |
| TC-PAR-002 | One source times out | mock with 10s timeout (threshold=2.5s) | Other sources return within 2.5s, timeout logged | [ ] |
| TC-PAR-003 | All sources time out | all mocks set to 10s | [] returned within ~2.5s, error logged, fallback triggered | [ ] |
| TC-PAR-004 | One source raises exception | mock raises ValueError | Other 3 sources return, exception logged | [ ] |

### 2.7 Weighted RRF (HS-REQ-002, HS-REQ-005)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-WRRF-001 | Weights applied — high-weight source dominates | policies w=1.0, sqlite w=0.0, same doc in both | Higher score for doc from policies source | [ ] |
| TC-WRRF-002 | Equal weights = standard RRF | all weights=1.0 | Same as TC-RRF-001 | [ ] |
| TC-WRRF-003 | Zero weight suppresses source | sqlite w=0.0 | sqlite results don't contribute to scores | [ ] |

### 2.8 Category Handling (HS-REQ-011)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-CAT-001 | account category skips hybrid | category="account" | `hybrid_search()` returns `""` | [ ] |
| TC-CAT-002 | skill_admin skips hybrid | category="skill_admin" | `hybrid_search()` returns `""` | [ ] |
| TC-CAT-003 | hr category includes MCP query | category="hr" | MCP query submitted in thread pool | [ ] |
| TC-CAT-004 | tech category skips MCP | category="tech" | MCP query not submitted | [ ] |

### 2.9 Distance-to-Score Conversion (HS-REQ-001)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-DSC-001 | ChromaDB distance 0.1 → score | distance=0.1 | score ≈ 0.9 (or 0.909 using 1/(1+d)) | [ ] |
| TC-DSC-002 | ChromaDB distance 0.9 → score | distance=0.9 | score ≈ 0.1 (or 0.526) | [ ] |
| TC-DSC-003 | Negative distance clipped | distance=-0.5 | score = 1.0 | [ ] |

### 2.10 SQLite Employee Filtering (HS-REQ-001)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-EMP-001 | Results filtered by employee_id | query with employee_id EMP0001 | Only results matching EMP0001 returned | [ ] |
| TC-EMP-002 | Cross-user leak prevented | employee_id EMP0002 queries tickets of EMP0001 | No EMP0001 results in output | [ ] |

---

## 3. Integration Tests — `tests/test_hybrid_search.py`

### 3.1 SQLite FTS5 Query (HS-REQ-001)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-SQL-001 | FTS5 matches across tickets table | query="conference expense rejected" | Matches tickets with "conference" or "expense" in title or description | [ ] |
| TC-SQL-002 | No FTS5 matches | query="zzznotfoundzzz" | Empty result list | [ ] |
| TC-SQL-003 | Category-filtered SQLite search | category="tech" | Only tech-related tickets returned (not HR tickets) | [ ] |
| TC-SQL-004 | FTS5 across expense_claims | query="appeal rejected" | Matches expense_claims with matching purpose/description | [ ] |
| TC-SQL-005 | Messages table FTS (history.db) | query="meeting room booking" | Matches chat history content | [ ] |

### 3.2 ChromaDB Score Return (HS-REQ-001)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-CHR-001 | Policy retrieval returns score | query="leave policy" | Results include distance/score field | [ ] |
| TC-CHR-002 | Few-shot retrieval returns score | query="how to apply leave" | Results include score field | [ ] |

### 3.3 MCP HTTP Query (HS-REQ-001)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-MCP-001 | MCP query returns leave history | query="leave" + employee_id | JSON-RPC response parsed correctly | [ ] |

---

## 4. E2E Tests — `tests/test_agents_hybrid.py`

### 4.1 Category Exclusion (HS-REQ-011)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-E2E-EXCL-001 | account query routes to fallback | "Change my password" with account category | RAG path used, no hybrid_context | [ ] |
| TC-E2E-EXCL-002 | skill_admin query routes to fallback | "Set LLM model" with skill_admin category | RAG path used, no hybrid_context | [ ] |

### 4.2 LangGraph Node Integration (HS-REQ-004, HS-REQ-006)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-E2E-001 | Hybrid search enabled — single turn | "What's the conference expense appeal policy?" | `hybrid_context` populated, `rag_context` + `fewshot_context` empty | [ ] |
| TC-E2E-002 | Hybrid search enabled — multi-source | "Has anyone appealed a rejected expense?" | `hybrid_context` contains results from policies + fewshot + sqlite | [ ] |
| TC-E2E-003 | Hybrid search disabled (config toggle) | Set enabled=false | Falls through to `rag_retrieval` → `fewshot_retrieval` path | [ ] |
| TC-E2E-004 | Hybrid search times out — fallback | All sources mocked to timeout | Falls to `rag_retrieval` → `fewshot_retrieval` path | [ ] |
| TC-E2E-005 | Hybrid search raises — fallback | hybrid_search_node raises | Falls to `rag_retrieval` → `fewshot_retrieval` path | [ ] |

### 4.3 Fallback Visibility & Config (HS-REQ-012, HS-REQ-013)

| TC ID | Description | Input | Expected Output | Status |
|-------|-------------|-------|-----------------|--------|
| TC-E2E-FBK-001 | Fallback event logged on timeout | All sources mocked to timeout | Log contains "hybrid_search fallback" entry | [ ] |
| TC-E2E-FBK-002 | Fallback ratio queryable via analytics | Simulate 10 queries, 2 fallbacks | `/analytics/data` shows 80% hybrid success rate | [ ] |
| TC-E2E-CFG-001 | Config state readable | Set rrf_k via set_skill_config, then get | `get_skill_config("hybrid_search")` returns current value | [ ] |

### 4.4 Zero Regression (HS-REQ-010)

| TC ID | Description | Verifies | Status |
|-------|-------------|----------|--------|
| TC-REG-001 | Existing chat: "What's my leave balance?" | Works with hybrid enabled (MCP + SQLite) AND with fallback | [ ] |
| TC-REG-002 | Existing chat: "Create an IT ticket for VPN access" | Works with hybrid enabled AND with fallback | [ ] |
| TC-REG-003 | Existing chat: "Show my payslip for March" | Works with hybrid enabled AND with fallback | [ ] |
| TC-REG-004 | Existing chat: "Book a meeting room tomorrow" | Works with hybrid enabled AND with fallback | [ ] |
| TC-REG-005 | Existing chat: "What expenses are pending approval?" | Works with hybrid enabled AND with fallback | [ ] |
| TC-REG-006 | Run full existing E2E test suite | All existing tests pass with `hybrid_search.enabled=true` | [ ] |
| TC-REG-007 | Run full existing E2E test suite | All existing tests pass with `hybrid_search.enabled=false` (fallback) | [ ] |

---

## 5. Performance Benchmarks (HS-REQ-009)

### 5.1 Benchmark Tests

| TC ID | Scenario | Target | Threshold (fail) | Status |
|-------|----------|--------|-----------------|--------|
| TC-BENCH-001 | All 4 sources fast (mock: 100ms each) | P95 < 500ms | > 1s | [ ] |
| TC-BENCH-002 | One source slow (mock: 2s sqlite) | P95 < 2.5s | > 4s | [ ] |
| TC-BENCH-003 | Real ChromaDB + SQLite (populated) | P95 < 3s | > 3.5s | [ ] |
| TC-BENCH-004 | RRF fusion on 100 results per source (400 total) | P95 < 200ms | > 500ms | [ ] |
| TC-BENCH-005 | Deduplication on 100 results | P95 < 50ms | > 100ms | [ ] |

### 5.2 Run Command

```bash
# Unit + integration tests
cd /workspaces/aiagentic-comp-frontdeskai && python -m pytest tests/test_hybrid_search.py -v

# E2E tests
python -m pytest tests/test_agents_hybrid.py -v

# Regression tests
python -m pytest tests/ -v --ignore=tests/test_hybrid_search.py --ignore=tests/test_agents_hybrid.py

# Benchmarks
python -m pytest tests/test_hybrid_search.py --benchmark-only --benchmark-json output.json

# All hybrid tests
python -m pytest tests/test_hybrid_search.py tests/test_agents_hybrid.py -v
```

---

## 6. RTM — Test Coverage Matrix

| Req ID | Unit Tests | Integration Tests | E2E Tests | Benchmarks |
|--------|-----------|------------------|-----------|------------|
| HS-REQ-001 | TC-PAR-001 through TC-PAR-004, TC-DSC-001 through TC-DSC-003, TC-EMP-001, TC-EMP-002 | TC-SQL-001 through TC-SQL-005, TC-CHR-001, TC-CHR-002, TC-MCP-001 | — | — |
| HS-REQ-002 | TC-RRF-001 through TC-RRF-006, TC-WRRF-001 through TC-WRRF-003 | — | — | TC-BENCH-004 |
| HS-REQ-003 | TC-DED-001 through TC-DED-005 | — | — | TC-BENCH-005 |
| HS-REQ-004 | TC-FMT-001 through TC-FMT-004 | — | TC-E2E-001, TC-E2E-002 | — |
| HS-REQ-005 | TC-WRRF-001 through TC-WRRF-003 | — | — | — |
| HS-REQ-006 | — | — | TC-E2E-003, TC-E2E-004, TC-E2E-005 | — |
| HS-REQ-007 | TC-COR-001 through TC-COR-003 | — | — | — |
| HS-REQ-008 | TC-ALR-001 through TC-ALR-004 | — | — | — |
| HS-REQ-009 | — | — | — | TC-BENCH-001 through TC-BENCH-005 |
| HS-REQ-010 | — | — | TC-REG-001 through TC-REG-007 | — |
| HS-REQ-011 | TC-CAT-001 through TC-CAT-004 | — | TC-E2E-EXCL-001, TC-E2E-EXCL-002 | — |
| HS-REQ-012 | — | — | TC-E2E-FBK-001, TC-E2E-FBK-002 | — |
| HS-REQ-013 | — | — | TC-E2E-CFG-001 | — |

---

## 7. Test Environment Setup

### 7.1 Fixtures Required

```python
@pytest.fixture
def mock_chromadb_policies(mocker):
    """Mock ChromaDB policy retrieval with controlled results."""

@pytest.fixture
def mock_chromadb_fewshot(mocker):
    """Mock ChromaDB few-shot retrieval with controlled results."""

@pytest.fixture
def mock_sqlite_fts(mocker):
    """Mock SQLite FTS5 queries."""

@pytest.fixture
def mock_mcp_server(mocker):
    """Mock MCP HTTP client (no real PostgreSQL dependency)."""

@pytest.fixture
def real_graph():
    """Build the LangGraph with hybrid_search node enabled."""
    from app.agents import build_graph
    return build_graph()
```

### 7.2 Dependencies

- `pytest>=7.0`
- `pytest-benchmark` (for performance tests)
- `pytest-mock` (for source mocking in unit tests)
- Existing test fixtures from `tests/conftest.py`

No new external dependencies.

---

## 8. Progress Tracking

- [ ] **TC-RRF-001** through **TC-RRF-006** — RRF fusion unit tests
- [ ] **TC-DED-001** through **TC-DED-005** — Deduplication unit tests
- [ ] **TC-FMT-001** through **TC-FMT-004** — Context formatting unit tests
- [ ] **TC-COR-001** through **TC-COR-003** — Correlation unit tests (P1)
- [ ] **TC-ALR-001** through **TC-ALR-004** — Alerting unit tests (P2)
- [ ] **TC-PAR-001** through **TC-PAR-004** — Parallel execution tests
- [ ] **TC-WRRF-001** through **TC-WRRF-003** — Weighted RRF tests
- [ ] **TC-CAT-001** through **TC-CAT-004** — Category handling tests
- [ ] **TC-DSC-001** through **TC-DSC-003** — Distance-to-score conversion tests
- [ ] **TC-EMP-001**, **TC-EMP-002** — Employee ID filtering tests
- [ ] **TC-SQL-001** through **TC-SQL-005** — SQLite FTS5 integration tests
- [ ] **TC-CHR-001**, **TC-CHR-002** — ChromaDB score integration tests
- [ ] **TC-MCP-001** — MCP query integration test
- [ ] **TC-E2E-001** through **TC-E2E-005** — LangGraph node E2E tests
- [ ] **TC-E2E-EXCL-001**, **TC-E2E-EXCL-002** — Category exclusion E2E tests
- [ ] **TC-E2E-FBK-001**, **TC-E2E-FBK-002** — Fallback visibility E2E tests
- [ ] **TC-E2E-CFG-001** — Config readback E2E test
- [ ] **TC-REG-001** through **TC-REG-007** — Zero regression E2E tests
- [ ] **TC-BENCH-001** through **TC-BENCH-005** — Performance benchmarks

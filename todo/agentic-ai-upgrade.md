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

- [ ] **1.1 Structured output for supervisor classifier**
  - Use `llm.with_structured_output()` with a Pydantic model for `category` + `confidence`
  - Eliminate fragile string parsing in `agents.py:48-61`
  - File: `agents.py` — `supervisor()` function

- [ ] **1.2 Use ChatPromptTemplate with system/user message roles**
  - Replace f-string prompts with proper `SystemMessage` / `HumanMessage` separation
  - Add few-shot examples to supervisor for consistent classification
  - Files: `agents.py` — all worker functions

- [ ] **1.3 Extract worker factory to eliminate code duplication**
  - HR, Tech, Finance, Facilities workers are nearly identical
  - Create `make_domain_worker(name, system_prompt, escalation_rule)` factory
  - File: `agents.py`

- [ ] **1.4 Fix async blocking**
  - Replace `compiled.invoke()` with `compiled.ainvoke()` or `asyncio.to_thread()`
  - File: `app.py:216`

- [ ] **1.5 Fix SQLite connection leak on error**
  - Use `try/finally` or context manager for the connection in `/chat/send`
  - File: `app.py:179-254`

### Phase 2: Conversation Memory
> Enable multi-turn conversations so agents understand context from previous messages.

- [ ] **2.1 Add `messages` to SupportRequest state**
  - Change state to include `messages: Annotated[list, add]` for conversation history
  - File: `agents.py` — `SupportRequest` TypedDict

- [ ] **2.2 Pass conversation history into agent prompts**
  - Load last N messages from history DB before invoking the graph
  - Include them in the initial state so agents see prior context
  - Files: `app.py` — `send_message()`, `agents.py` — worker prompts

- [ ] **2.3 Use LangGraph message-based state**
  - Migrate from custom state dict to LangGraph's `MessagesState` pattern
  - Leverage the checkpointer properly for turn-by-turn accumulation
  - Files: `agents.py`, `app.py`

### Phase 3: RAG / Knowledge Grounding
> Ground agent responses in actual documents instead of hardcoded prompt strings.

- [ ] **3.1 Create knowledge base documents**
  - Populate `data/` with company policy documents (HR handbook, IT SLAs, finance policies, facilities guide)
  - Format: Markdown or PDF files, one per domain
  - Directory: `data/`

- [ ] **3.2 Add vector store and embedding pipeline**
  - Add FAISS or ChromaDB as vector store
  - Use embedding model (Groq or HuggingFace) to index documents
  - Create `rag.py` module for document loading, chunking, embedding, retrieval
  - New file: `rag.py`, update `requirements.txt`

- [ ] **3.3 Integrate RAG as a tool for domain workers**
  - Each worker retrieves relevant policy sections before generating a response
  - Worker prompts include retrieved context: "Based on the following policy documents: ..."
  - File: `agents.py` — all worker functions

- [ ] **3.4 Add source citations to responses**
  - Include document name and section in the final response
  - Allow users to see what policy was referenced
  - Files: `agents.py` — `finalize()`, `templates/chat.html`

### Phase 4: Tool Use / Function Calling
> Give agents the ability to take real actions and look up real data.

- [ ] **4.1 Define tool schemas**
  - `get_leave_balance(employee_id)` — HR tool
  - `create_jira_ticket(summary, priority)` — Tech tool
  - `get_expense_status(claim_id)` — Finance tool
  - `book_meeting_room(room, date, time)` — Facilities tool
  - New file: `tools.py`

- [ ] **4.2 Implement tool stubs/mock backends**
  - SQLite-backed mock data for leave balances, expense claims, etc.
  - Allows demo/competition without real backend integrations
  - New file: `tools.py`, mock data in `data/`

- [ ] **4.3 Bind tools to LLM and enable function calling**
  - Use `llm.bind_tools([...])` for each domain worker
  - LLM decides when to call tools vs. respond directly
  - File: `agents.py` — worker functions

- [ ] **4.4 Add tool execution node to the graph**
  - Add a `ToolNode` or custom executor after worker decides to call a tool
  - Handle tool results and feed back into the LLM for final response
  - File: `agents.py` — `build_graph()`

### Phase 5: Reasoning Loop (ReAct Pattern)
> Enable agents to think, act, observe, and iterate — making them truly agentic.

- [ ] **5.1 Add ReAct loop to domain workers**
  - Replace single-shot LLM calls with a think → act → observe cycle
  - Agent can call tools, evaluate results, and decide next step
  - File: `agents.py`

- [ ] **5.2 Add max iteration guard**
  - Prevent infinite loops with a configurable max_iterations (e.g., 5)
  - Fall back to template response if max iterations exceeded
  - File: `agents.py`

- [ ] **5.3 Add self-correction on QA failure**
  - If QA fails, route back to the worker with the QA feedback instead of straight to fallback
  - Allow one retry before falling back
  - File: `agents.py` — `route_qa()`, `build_graph()`

### Phase 6: Guardrails & Security
> Harden the system for production use.

- [ ] **6.1 Move credentials to environment variables**
  - `AUTH_PASSWORD` from env var, not hardcoded
  - Ensure `SECRET_KEY` default is never used in production
  - File: `app.py:23-24`

- [ ] **6.2 Add rate limiting**
  - Rate limit `/login` (prevent brute force) and `/chat/send` (prevent abuse)
  - Use `slowapi` or similar middleware
  - File: `app.py`, update `requirements.txt`

- [ ] **6.3 Add PII detection guardrail**
  - Scan LLM responses for PII (SSN, phone, email) before sending to user
  - Add as a step in QA check or as a separate guardrail node
  - File: `agents.py` — `qa_check()`

- [ ] **6.4 Fix K8s security context**
  - Add `runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`
  - File: `k8s/deployment.yaml`

- [ ] **6.5 Fix deploy script `sed -i` mutation**
  - Use `envsubst` or temporary files instead of mutating source YAMLs
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

**Last updated:** 2026-02-19

| Phase | Status | Notes |
|---|---|---|
| Phase 1: Structured Output | Not Started | |
| Phase 2: Conversation Memory | Not Started | |
| Phase 3: RAG | Not Started | |
| Phase 4: Tool Use | Not Started | |
| Phase 5: ReAct Loop | Not Started | |
| Phase 6: Guardrails | Not Started | |

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

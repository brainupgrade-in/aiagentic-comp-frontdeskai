# Langfuse Integration — FrontDesk AI

Langfuse provides LLM-specific observability: prompt tracing, token usage, latency per generation, user sessions, and cost tracking. It complements the OpenTelemetry stack (Tempo/Prometheus/Loki) by adding LLM-aware analytics.

## Architecture

```
FrontDesk AI (FastAPI + LangGraph)
│
├── OpenTelemetry ──→ Tempo/Prometheus/Loki ──→ Grafana
│   (infrastructure observability: spans, metrics, logs)
│
└── Langfuse ──→ Langfuse Cloud (https://cloud.langfuse.com)
    (LLM observability: prompts, completions, tokens, cost, sessions)
```

Both systems run in parallel. OpenTelemetry captures infrastructure-level spans and metrics. Langfuse captures LLM-level traces with full prompt/completion content, token counts, and model metadata.

## How It Works

### LangChain Callback Handler

Langfuse integrates via the `LangfuseCallbackHandler` — a LangChain-compatible callback that intercepts every LLM invocation in the LangGraph agent chain.

```
User Request → app.py
  │
  ├── Creates LangfuseCallbackHandler (per request)
  │     user_id = email, session_id = email
  │
  └── compiled.invoke(state, config={"callbacks": [lf_handler]})
        │
        ├── supervisor → llm.invoke(prompt)  ← Langfuse captures
        ├── hr_worker  → llm.invoke(prompt)  ← Langfuse captures
        ├── manager    → llm.invoke(prompt)  ← Langfuse captures
        └── ...
```

Each `llm.invoke()` call through `ChatGroq` triggers the callback, which sends:
- Full prompt text
- Full completion text
- Token usage (input, output, total)
- Model name (`llama-3.3-70b-versatile`)
- Latency
- User and session IDs

### Code Flow

**`observability.py`** — Initialization and handler factory:

```python
from langfuse.callback import CallbackHandler as LangfuseCallbackHandler

langfuse_enabled = False  # Set True during init if env vars present

def init_observability():
    # ... OTel setup ...

    # Langfuse — enabled only when all three env vars are set
    lf_secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    lf_public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    lf_host = os.environ.get("LANGFUSE_HOST", "")
    if lf_secret and lf_public and lf_host:
        langfuse_enabled = True

def get_langfuse_handler(user_id="", session_id=""):
    if not langfuse_enabled:
        return None
    return LangfuseCallbackHandler(
        user_id=user_id,
        session_id=session_id,
    )
```

**`app.py`** — Per-request callback attachment:

```python
from observability import get_langfuse_handler

# Inside send_message():
config = {"configurable": {"thread_id": user}}

lf_handler = get_langfuse_handler(user_id=user, session_id=user)
if lf_handler:
    config["callbacks"] = [lf_handler]

result = compiled.invoke(initial_state, config)
```

## Configuration

### Environment Variables

| Variable | Required | Example | Description |
|----------|----------|---------|-------------|
| `LANGFUSE_SECRET_KEY` | Yes | `sk-lf-9327d358-...` | Langfuse project secret key |
| `LANGFUSE_PUBLIC_KEY` | Yes | `pk-lf-04cea526-...` | Langfuse project public key |
| `LANGFUSE_HOST` | Yes | `https://cloud.langfuse.com` | Langfuse server URL |

All three must be set for Langfuse to activate. If any are missing, the app runs normally without Langfuse (no errors, no warnings beyond a startup log).

### Getting Langfuse Keys

1. Sign up at [https://cloud.langfuse.com](https://cloud.langfuse.com)
2. Create a new project (e.g., "FrontDesk AI")
3. Go to **Settings → API Keys**
4. Copy the **Secret Key** and **Public Key**

### Setting Up `.env`

```bash
cp .env.example .env
```

Add your keys to `.env`:

```env
GROQ_API_KEY=gsk_your_groq_key_here
LANGFUSE_SECRET_KEY=sk-lf-your-secret-key
LANGFUSE_PUBLIC_KEY=pk-lf-your-public-key
LANGFUSE_HOST=https://cloud.langfuse.com
```

### Kubernetes Deployment

The `deploy.sh` script reads `.env` and creates a K8s secret with all keys:

```bash
bash k8s/deploy.sh
```

The deployment manifest mounts Langfuse env vars from the secret with `optional: true`, so the app starts even if Langfuse keys are absent:

```yaml
- name: LANGFUSE_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: frontdeskai-secret
      key: LANGFUSE_SECRET_KEY
      optional: true
```

## What Langfuse Captures

### Per Trace (one per chat request)

| Field | Source | Example |
|-------|--------|---------|
| Trace ID | Auto-generated | `a1b2c3d4-...` |
| User ID | Email from JWT | `loadtest@test.com` |
| Session ID | Email (groups conversation) | `loadtest@test.com` |
| Total Latency | Start to end of graph | `1490ms` |
| Total Tokens | Sum across all LLM calls | `847` |
| Total Cost | Based on model pricing | `$0.0012` |

### Per Generation (one per LLM call)

| Field | Source | Example |
|-------|--------|---------|
| Model | `ChatGroq` config | `llama-3.3-70b-versatile` |
| Prompt | Full prompt text | `"You are UniGPS HR..."` |
| Completion | Full response text | `"You have 24 casual leaves..."` |
| Input Tokens | Groq API response | `156` |
| Output Tokens | Groq API response | `89` |
| Latency | Call duration | `472ms` |
| Agent Name | LangGraph node name | `hr_worker` |

### Agent Chain Visibility

For a typical HR request with escalation, Langfuse shows:

```
Trace: "What is the leave policy?"
├── Generation: supervisor        (156 → 24 tokens, 372ms)
├── Generation: hr_worker         (189 → 89 tokens, 472ms)
└── Generation: manager           (210 → 76 tokens, 410ms)
    Total: 555 → 189 tokens, 1254ms
```

## Langfuse Dashboard

After sending traffic, the Langfuse web UI shows:

### Traces View
- List of all chat requests with latency, token count, user
- Click any trace to see the full agent chain
- Filter by user, session, time range

### Generations View
- Every individual LLM call across all traces
- Sort by latency, tokens, cost
- Full prompt and completion text visible

### Sessions View
- Grouped by user email (session_id)
- Shows conversation history per user
- Token usage trends over time

### Metrics
- **Latency distribution** — p50/p95/p99 per model
- **Token usage** — input vs output over time
- **Cost tracking** — per model, per user
- **Error rate** — failed LLM calls

## Verifying the Integration

### 1. Check Startup Logs

```bash
kubectl logs deployment/frontdeskai --tail=10 | head -5
```

Look for:
```json
{"message": "Langfuse enabled", "langfuse_host": "https://cloud.langfuse.com"}
```

If you see `"Langfuse disabled"` instead, check that all three env vars are set in the K8s secret.

### 2. Send a Test Request

```bash
FRONTDESKAI_URL=https://<NAMESPACE>-app.brainupgrade.in \
  bash scripts/generate-test-traffic.sh 1 0
```

### 3. Check Langfuse Cloud

Go to [https://cloud.langfuse.com](https://cloud.langfuse.com) → your project → **Traces**.

You should see a new trace with:
- User: the email used for login
- Generations: 2-3 (supervisor + worker + possibly manager)
- Model: `llama-3.3-70b-versatile`

### 4. Verify via Pod Logs

After a chat request, the logs confirm both OTel and Langfuse are active:

```json
{"message": "LLM call completed", "trace_id": "9020e4fc...", "agent": "supervisor", "duration_ms": 371.8}
{"message": "LLM call completed", "trace_id": "9020e4fc...", "agent": "hr_worker", "duration_ms": 471.6}
{"message": "Chat request processed", "trace_id": "9020e4fc...", "category": "hr", "duration_ms": 1359.4}
```

The `trace_id` in these logs is the OTel trace ID. The Langfuse trace ID is separate (auto-generated by the Langfuse SDK).

## Langfuse vs OpenTelemetry

Both systems capture observability data, but serve different purposes:

| Aspect | OpenTelemetry (Tempo/Prometheus/Loki) | Langfuse |
|--------|---------------------------------------|----------|
| **Focus** | Infrastructure observability | LLM-specific observability |
| **Traces** | Span waterfall (timing, errors) | Prompt/completion content |
| **Metrics** | Custom counters/histograms | Auto token/cost/latency |
| **Logs** | Structured JSON with correlation | Not applicable |
| **Prompt visibility** | No (only span attributes) | Full prompt + completion text |
| **Cost tracking** | Manual (compute from tokens) | Automatic per model |
| **Session grouping** | Not built-in | By user/session ID |
| **Self-hosted** | Yes (Tempo, Prometheus, Loki) | Cloud or self-hosted |
| **Dashboard** | Grafana | Langfuse web UI |

**Use both together**: OpenTelemetry for infrastructure health (latency, error rates, resource usage) and Langfuse for LLM quality (prompt engineering, token optimization, cost management).

## Dependencies

```
# requirements.txt
langchain>=0.3.0
langfuse==2.51.3
```

`langfuse` requires the full `langchain` package (not just `langchain-core`) for its callback handler integration.

## Disabling Langfuse

To disable Langfuse without code changes, remove or clear the env vars:

```bash
# Clear from K8s secret
kubectl create secret generic frontdeskai-secret \
  --from-literal=GROQ_API_KEY="..." \
  --from-literal=SECRET_KEY="..." \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart the app
kubectl rollout restart deployment/frontdeskai
```

Or simply remove the `LANGFUSE_*` lines from `.env` and redeploy.

## Use Case: Detecting an Infinite LLM Loop Before It Burns Thousands of Dollars

### The Scenario

A developer modifies the FrontDesk AI agent graph to add a "retry with better prompt" feature. The intent is simple: if `qa_check` fails, instead of falling back to a template response, re-route back to the worker agent with an improved prompt.

The code change looks harmless:

```python
# BUGGY CODE — qa_check routes back to worker on failure instead of fallback
def route_qa(state: SupportRequest) -> str:
    if state["error"]:
        return state["category"]  # retry the worker ← BUG
    return "finalize"
```

```python
# Graph edges (buggy)
graph.add_conditional_edges("qa_check", route_qa, {
    "finalize": "finalize",
    "hr": "hr_worker",        # ← loops back
    "tech": "tech_worker",    # ← loops back
    "finance": "finance_worker",
    "facilities": "facilities_worker",
    "general": "general_worker",
})
```

The problem: if the worker consistently produces output that fails QA (e.g., a prompt that always generates a response shorter than 20 characters), the graph enters an infinite cycle:

```
supervisor → hr_worker → escalation_check → qa_check → hr_worker → escalation_check → qa_check → hr_worker → ...
                                                  ↑___________________________|
```

Each cycle makes **2 LLM calls** (worker + potentially manager). With Groq's llama-3.3-70b consuming ~500 tokens per call, the loop burns through tokens at an alarming rate.

### The Cost Impact

| Duration | Cycles | LLM Calls | Tokens | Cost (GPT-4 pricing*) |
|----------|--------|-----------|--------|-----------------------|
| 1 minute | ~60 | ~120 | ~60,000 | $1.80 |
| 1 hour | ~3,600 | ~7,200 | ~3.6M | $108 |
| Overnight (8 hrs) | ~28,800 | ~57,600 | ~28.8M | $864 |
| Weekend (48 hrs) | ~172,800 | ~345,600 | ~172M | **$5,184** |

*Groq is free-tier, but the same bug on GPT-4o ($5/1M input, $15/1M output) or Claude would cost thousands. This is a realistic production scenario.*

Even on Groq's free tier, the loop would hit rate limits (30 req/min), causing the request to hang indefinitely and potentially blocking other users.

### Without Langfuse: How the Bug Hides

Traditional monitoring alone struggles to catch this:

- **Prometheus metrics** show elevated `frontdeskai_llm_tokens_total` but the counter is cumulative — a gradual increase looks normal during active use
- **Grafana dashboards** show the request duration spiking, but if nobody is watching the dashboard at that moment, the alert may not fire until the damage is done
- **Application logs** (Loki) flood with repeated `LLM call completed` entries, but log volume alone doesn't clearly signal "this is a loop" — it could just be a busy period
- **OTel traces** (Tempo) show a single span `chat.send` with many child spans, but you'd have to manually open the trace to notice the pattern

The fundamental problem: **none of these tools show you that the same prompt is being sent to the same agent over and over**.

### With Langfuse: Detection in Under 60 Seconds

#### Step 1: Immediate Alert — Anomalous Generation Count

On the Langfuse **Traces** page, you instantly see something wrong:

```
Normal trace:    3 generations, 847 tokens, 1.4s
Normal trace:    2 generations, 523 tokens, 0.9s
BUGGY trace:   147 generations, 73,500 tokens, 245s  ← stands out immediately
```

The generation count alone is a smoking gun. Normal FrontDesk AI requests produce 2-4 generations (supervisor + worker + optionally manager). **147 generations is impossible in a healthy graph.**

#### Step 2: Root Cause in One Click — Prompt Repetition

Click the anomalous trace. Langfuse shows the full generation chain:

```
Trace: "What is the leave policy?"  —  147 generations, 73,500 tokens
│
├── Generation 1: supervisor     (156 → 24 tokens)   "CATEGORY: hr, CONFIDENCE: 9"
├── Generation 2: hr_worker      (189 → 12 tokens)   "Check HR portal."        ← too short!
├── Generation 3: hr_worker      (189 → 14 tokens)   "Visit HR portal."        ← retry, still short
├── Generation 4: hr_worker      (189 → 11 tokens)   "See HR policy."          ← retry, still short
├── Generation 5: hr_worker      (189 → 13 tokens)   "Ask HR team."            ← retry, still short
│   ... (142 more identical hr_worker calls)
└── Generation 147: hr_worker    (189 → 12 tokens)   "Contact HR."             ← still looping
```

The root cause is immediately visible:
1. **Same agent** (`hr_worker`) called 146 times in a row
2. **Same prompt** sent every time (the worker prompt doesn't change between retries)
3. **Output always fails QA** (< 20 characters) because the prompt says "Reply helpfully in 2-3 sentences" but certain edge-case inputs produce terse responses

#### Step 3: Quantify the Blast Radius

Langfuse **Metrics** tab shows:

- **Token usage spike** at the exact timestamp
- **Cost per trace** — this single trace consumed more tokens than the previous 100 traces combined
- **User impact** — the affected user's session was blocked for 4+ minutes
- **Model** — confirms it's `llama-3.3-70b-versatile` (if on a paid model, shows dollar cost)

#### Step 4: Fix and Verify

The fix is straightforward once you see the loop pattern:

```python
# FIXED — add a retry counter to prevent infinite loops
MAX_RETRIES = 2

def qa_check(state: SupportRequest) -> dict:
    output = state["worker_output"]
    retry_count = state.get("qa_retry_count", 0)

    if len(output) < 20 and retry_count < MAX_RETRIES:
        return {"error": "too short", "qa_retry_count": retry_count + 1}
    elif len(output) < 20:
        return {"error": "too short after retries"}  # goes to fallback
    return {"error": ""}
```

Or better — keep the original safe design that routes QA failures to `fallback` (a static template), never back to the worker:

```python
# SAFE — the original FrontDesk AI design
def route_qa(state: SupportRequest) -> str:
    return "fallback" if state["error"] else "finalize"
```

After deploying the fix, send another test request and check Langfuse:

```
Fixed trace:     3 generations, 847 tokens, 1.4s  ← back to normal
```

### Key Langfuse Features That Enable This Detection

| Feature | What It Shows | Why It Matters |
|---------|---------------|---------------|
| **Generation count per trace** | 147 vs normal 2-4 | Instantly flags runaway loops |
| **Full prompt text** | Same prompt repeated 146 times | Confirms it's a loop, not diverse traffic |
| **Full completion text** | Short responses that fail QA | Shows why the loop doesn't terminate |
| **Token usage per trace** | 73,500 vs normal 500-800 | Quantifies the cost of the bug |
| **Agent/model name per generation** | `hr_worker` called 146× | Pinpoints which agent is looping |
| **Timeline view** | Generations stacked with ~1s gaps | Visual pattern of repetitive calls |
| **Session grouping** | Only 1 user affected | Limits blast radius assessment |

### Prevention Checklist

After catching this bug, implement these safeguards:

1. **LangGraph recursion limit** — set `compiled.invoke(state, config, recursion_limit=25)` to hard-cap graph steps
2. **Langfuse alerts** — set up a webhook alert when generation count per trace exceeds 10
3. **Token budget per request** — check cumulative tokens in `trace_llm_call()` and abort if threshold exceeded
4. **No retry loops in agent graphs** — QA failures should go to `fallback`, never back to workers
5. **Grafana alert on token rate** — `rate(frontdeskai_llm_tokens_total[1m]) > 1000` triggers PagerDuty

### Summary

| | Without Langfuse | With Langfuse |
|--|-----------------|---------------|
| **Time to detect** | Hours (maybe days if on weekends) | < 60 seconds |
| **Root cause identification** | Read thousands of log lines, correlate spans | One click on the anomalous trace |
| **Blast radius assessment** | Query Prometheus, estimate from counters | Exact token count and cost in UI |
| **Cost of the bug (GPT-4, 8hrs)** | **$864** before anyone notices | **$1.80** (caught in first minute) |

This is why Langfuse is essential for agentic AI systems: **traditional observability tells you something is wrong; Langfuse tells you exactly what the LLM is doing wrong and why**.

---

## Troubleshooting

### "Langfuse disabled" in startup logs

All three env vars must be non-empty:
```bash
kubectl get secret frontdeskai-secret -o jsonpath='{.data.LANGFUSE_SECRET_KEY}' | base64 -d
kubectl get secret frontdeskai-secret -o jsonpath='{.data.LANGFUSE_PUBLIC_KEY}' | base64 -d
kubectl get secret frontdeskai-secret -o jsonpath='{.data.LANGFUSE_HOST}' | base64 -d
```

### No traces appearing in Langfuse Cloud

1. Verify the app logs show `"Langfuse enabled"` at startup
2. Send a chat request (health checks don't trigger Langfuse)
3. Langfuse batches events — traces may take 5-10 seconds to appear
4. Check Langfuse project settings match the keys in `.env`

### ModuleNotFoundError: langchain

The `langfuse` callback handler requires the full `langchain` package:
```
pip install langchain>=0.3.0
```

This is already included in `requirements.txt`.

### Langfuse adds latency

The `LangfuseCallbackHandler` sends data asynchronously in the background. It should not add noticeable latency to request processing. If latency increases, check network connectivity from the cluster to `cloud.langfuse.com`.

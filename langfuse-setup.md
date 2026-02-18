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

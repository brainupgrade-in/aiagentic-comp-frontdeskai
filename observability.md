# Observability — FrontDesk AI

FrontDesk AI uses OpenTelemetry for distributed tracing, Prometheus-compatible metrics, and structured JSON logging with trace correlation.

## Architecture

```
FrontDesk AI (FastAPI)
│
├── Traces ──→ OTLP gRPC ──→ Tempo ──→ Grafana
│   (BatchSpanProcessor)      :4317      Explore / Dashboard
│
├── Metrics ──→ /metrics ──→ Prometheus ──→ Grafana
│   (PrometheusMetricReader)   scrape      Dashboard
│
└── Logs ──→ stdout (JSON) ──→ Promtail ──→ Loki ──→ Grafana
    (JsonFormatter)             DaemonSet    :3100    Explore / Dashboard
                                    ↕
                    Correlated via trace_id + span_id
```

**Live on the workshop cluster since 2026-09-11.** All three legs are deployed in
namespace `monitoring`, and the trace ↔ log jump works in **both** directions:

| Direction | Mechanism | Where it is configured |
|---|---|---|
| log line → trace | Loki `derivedFields`, a regex over the line | Grafana datasource `loki` |
| span → log lines | Tempo `tracesToLogsV2`, a line-filter query | Grafana datasource `tempo` |

⚠️ **Neither indexes `trace_id`, deliberately.** It stays inside the JSON line; only
`level`, `logger` and `agent` are promoted to Loki labels by Promtail's pipeline. One
label value per request is the textbook way to blow up a Loki index.

⚠️ **A `${...}` in a Grafana *provisioning* file is expanded as an environment
variable** before the datasource is created, so `url: '${__value.raw}'` silently stored
an empty link target and the **View trace** button went nowhere — with no error
anywhere. Escape it: `'$${__value.raw}'`. Same for `'$${__span.traceId}'`.

## Span Hierarchy

Each chat request creates a parent span with child spans for each LLM agent call:

```
chat.send (parent)                    ← app/app.py
├── llm.supervisor                    ← app/agents.py (classification)
├── llm.<worker>                      ← app/agents.py
│   (hr_worker, tech_worker, finance_worker, facilities_worker,
│    analytics_worker, account_worker, skill_admin_worker, general_worker)
├── llm.manager                       ← app/agents.py (if escalated)
└── (all logs correlated via trace_id/span_id)
```

Retries add a second `llm.<worker>` span for the same agent — the QA gate can send a worker
back once (`MAX_QA_RETRIES = 1`) before falling through to the static fallback template.

### Span Attributes

| Span | Attributes |
|------|-----------|
| `chat.send` | `user.email`, `chat.category`, `chat.confidence`, `chat.escalated` |
| `llm.<agent>` | `agent.name`, `llm.tokens` |

## Metrics

Exposed at `GET /metrics` (Prometheus format).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `frontdeskai_llm_call_duration_seconds` | Histogram | `agent` | LLM call latency per agent |
| `frontdeskai_llm_tokens_total` | Counter | `agent` | Total LLM tokens consumed |
| `frontdeskai_category_total` | Counter | `category` | Requests by category (hr, tech, finance, etc.) |
| `frontdeskai_escalations_total` | Counter | `category` | Requests escalated to manager |
| `frontdeskai_fallbacks_total` | Counter | `category` | Fallback template responses |
| `frontdeskai_agent_errors_total` | Counter | `agent` | Agent errors |
| `frontdeskai_request_duration_seconds` | Histogram | `category` | End-to-end `/chat/send` latency |

⚠️ The three counters above are only exported **after their first increment** — OpenTelemetry's
Prometheus exporter does not emit a zero-valued series for an instrument that has never fired. A
dashboard panel reading them needs `or vector(0)`, or it shows "No data" on a healthy app.

⚠️ **The `agent` label encodes the trajectory, not just the agent.** A worker's ReAct loop emits
`<worker>_react_iter_<n>` per think-act-observe round, `<worker>_react_iter_<n>_fb` when the tool
call raised, and `<worker>_worker_final` for the answer. It is bounded by `MAX_TOOL_ITERATIONS`
(3), so cardinality is finite — and a series tagged `_react_iter_2` means that worker used the last
iteration it had.

### PromQL Examples

> ⚠️ **These require an image built after the bucket fix (2026-09-11).** Both histograms record
> seconds; until that fix they carried no explicit boundaries, so OpenTelemetry applied its default
> *millisecond* boundaries (`0, 5, 10, 25 … 10000`) and every observation landed in one bucket —
> measured on a live pod, `supervisor` had 12 calls totalling 18.8s with **all 12 in `le=5.0`**, so
> p50, p95 and p99 all returned the same fabricated value. `_LATENCY_BUCKETS` in
> `app/observability.py` now sets second-scale boundaries via a `View`. **A pod running an older
> image still reports the old buckets**, so redeploy before trusting a percentile.

```promql
# p95 request latency
histogram_quantile(0.95, sum(rate(frontdeskai_request_duration_seconds_bucket[5m])) by (le))

# LLM call duration by agent (p95)
histogram_quantile(0.95, sum(rate(frontdeskai_llm_call_duration_seconds_bucket[5m])) by (le, agent))

# mean seconds per call by agent -- correct today, and what the workshop dashboard uses
sum by (agent) (frontdeskai_llm_call_duration_seconds_sum)
  / sum by (agent) (frontdeskai_llm_call_duration_seconds_count)

# Token consumption rate per minute
sum(rate(frontdeskai_llm_tokens_total[5m])) by (agent) * 60

# Category distribution
sum by (category) (frontdeskai_category_total)

# Error rate
sum(rate(frontdeskai_agent_errors_total[5m]))
```

## Structured Logging

Logs are emitted as JSON to stdout with trace correlation fields:

```json
{
  "timestamp": "2026-02-19 01:30:45,123",
  "level": "INFO",
  "logger": "frontdeskai",
  "message": "LLM call completed",
  "trace_id": "c5ce75262ae82839630ac012830e1377",
  "span_id": "a1b2c3d4e5f67890",
  "agent": "hr_worker",
  "duration_ms": 397.2,
  "tokens": 222
}
```

### LogQL Examples

```logql
# All app logs
{app="frontdeskai"}

# Errors only
{app="frontdeskai"} |= "ERROR"

# Parse JSON and filter by agent
{app="frontdeskai"} | json | agent="supervisor"

# Logs with trace correlation
{app="frontdeskai"} | json | trace_id != "0"

# Slow LLM calls (>1s)
{app="frontdeskai"} | json | duration_ms > 1000
```

## Dependencies

```
# app/requirements.txt
opentelemetry-api==1.27.0
opentelemetry-sdk==1.27.0
opentelemetry-exporter-otlp-proto-grpc==1.27.0
opentelemetry-exporter-prometheus==0.48b0
prometheus-client==0.21.0
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_SERVICE_NAME` | `frontdeskai` | Service name in traces and resource attributes |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://tempo.monitoring.svc.cluster.local:4317` | Tempo OTLP gRPC endpoint |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

All observability configuration is externalized via environment variables set in `scripts/manifests/deployment.yaml`. No observability endpoints or service names are hardcoded in the application code.

### Kubernetes Pod Annotations

**Both** manifest sets carry these annotations — `scripts/manifests/deployment.yaml` and
`scripts/manifests/spark/deployment.yaml`, the latter being what `deploy-spark.sh` applies. The
`sed` pipeline in that script passes them through untouched; verified by rendering it.

```yaml
spec:
  template:
    metadata:
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: "/metrics"
```

⚠️ **A pod deployed before those lines existed keeps the old template.** A live pod was found on
2026-09-11 with no annotations at all, because it had been deployed minutes before the spark
manifest gained them. `kubectl apply` fixes it on the next `deploy-spark.sh`, but until then the
running object and the manifest disagree — check the pod, not the file, when discovery misses an app.

The **workshop platform** does not depend on these annotations either way. Its
`frontdeskai-participants` scrape job matches on the pod **label** `app=frontdeskai` within an
`agenticaiu<N>` namespace, which is one fewer thing that has to be right in what a participant
deploys. It also relabels the namespace onto a `namespace` label, which is what the *FrontDesk AI —
Agent Performance* dashboard filters on.

## Code Structure

| File | Observability Role |
|------|--------------------|
| `app/observability.py` | Central module: TracerProvider, MeterProvider, JsonFormatter, `trace_llm_call` context manager |
| `app/app.py` | Calls `init_observability()`, creates `chat.send` parent span, records request metrics, mounts `/metrics` |
| `app/agents.py` | Each agent function uses `trace_llm_call("agent_name")` to create child spans |

### Key Functions

```python
# Initialize (called once at app startup)
from observability import init_observability
init_observability()

# Get tracer for manual spans
from observability import get_tracer
tracer = get_tracer()
with tracer.start_as_current_span("my.operation") as span:
    span.set_attribute("key", "value")

# Trace an LLM call (auto-records duration, tokens, errors)
from observability import trace_llm_call
with trace_llm_call("agent_name") as ctx:
    response = llm.invoke(prompt)
    ctx["response"] = response  # enables token extraction

# Mount metrics endpoint
from observability import get_metrics_app
app.mount("/metrics", get_metrics_app())
```

## Optimizations & Known Pitfalls

### Python Module Import — Stale Counter References

**Bug**: Importing OTel counter variables at module load time captures `None` because `init_observability()` hasn't run yet.

```python
# BROKEN — captures None at import time, stays None forever
from observability import category_counter, escalation_counter, request_duration

# In request handler:
if category_counter:        # Always False — still None
    category_counter.add(1) # Never executes
```

**Fix**: Import the module and access counters as live attributes:

```python
# CORRECT — attribute lookup at call time gets the real counter
import observability as obs

# In request handler:
if obs.category_counter:        # True after init_observability()
    obs.category_counter.add(1) # Works
```

This applies to all module-level variables set during `init_observability()`: `category_counter`, `escalation_counter`, `fallback_counter`, `error_counter`, `request_duration`, `logger`.

### OTel Histogram Bucket Boundaries

OTel's default histogram boundaries are `[0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000]` (milliseconds for duration instruments). For **seconds-based** duration histograms like `frontdeskai_request_duration_seconds`, these translate to boundaries at 0s, 5s, 10s, 25s... which are far too coarse for sub-second LLM calls.

**Impact on dashboards**:
- `histogram_quantile(0.95, ...)` returns NaN or misleading values
- All requests fall in the `le="5"` bucket, giving no granularity

**Dashboard workaround**: Use `sum/count` average instead of percentiles:
```promql
sum(metric_sum{...}) / clamp_min(sum(metric_count{...}), 1)
```

**Proper fix** (if needed): Configure explicit bucket boundaries in `observability.py`:
```python
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation

view = View(
    instrument_name="frontdeskai_request_duration_seconds",
    aggregation=ExplicitBucketHistogramAggregation(
        boundaries=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
    ),
)
```

### Prometheus Scrape Annotations

Annotations must be on the **pod template** (`spec.template.metadata.annotations`), not the
Deployment metadata. Without them, Prometheus will not discover or scrape the `/metrics` endpoint,
even though the app exposes it correctly. If you ever need to add them to a live deployment:

```bash
kubectl patch deployment frontdeskai --type=merge -p '{
  "spec": {"template": {"metadata": {"annotations": {
    "prometheus.io/scrape": "true",
    "prometheus.io/port": "8000",
    "prometheus.io/path": "/metrics"
  }}}}
}'
```

### OTLP Exporter — Insecure Mode

The `OTLPSpanExporter` must be configured with `insecure=True` for in-cluster communication to Tempo (no TLS):

```python
otlp_exporter = OTLPSpanExporter(
    endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT",
                            "http://tempo.monitoring.svc.cluster.local:4317"),
    insecure=True
)
```

Without `insecure=True`, the exporter attempts TLS and fails silently — traces are dropped with no error in app logs.

### BatchSpanProcessor Flush

`BatchSpanProcessor` batches spans and flushes periodically (default: every 5 seconds, or when batch reaches 512 spans). For low-traffic training apps:
- Traces may take up to 5 seconds to appear in Tempo after a request completes
- On graceful shutdown, `TracerProvider.shutdown()` flushes remaining spans — but container kill signals may not allow enough time
- For debugging, set `OTEL_BSP_SCHEDULE_DELAY=1000` to flush every second

## Where this actually runs

⚠️ **`scripts/install-observability.sh` is for a local kind cluster** — its own header
says so, and it refuses politely if the current context does not look like kind. Do not
run it against the workshop cluster.

On the **workshop cluster** the same stack exists, deployed separately into the
`monitoring` namespace and shared by all participants:

| | endpoint | notes |
|---|---|---|
| Tempo | `tempo.monitoring.svc:4317` (OTLP gRPC) | `OTEL_SERVICE_NAME` carries the namespace, so your traces are findable |
| Loki | `loki.monitoring.svc:3100` | Promtail DaemonSet tails every pod; **since 2026-09-11** |
| Prometheus | scraped by the platform | the `frontdeskai-participants` job, matching pod label `app=frontdeskai` |
| Grafana | datasources `loki`, `tempo`, `prometheus-nuc` | log lines carry a **View trace** link into Tempo |

In Grafana's Explore, `{namespace="<your namespace>", app="frontdeskai"}` is your app's
logs; add `agent=~".+"` for just the LLM-call lines, which are labelled per agent.

⚠️ **`trace_id` is not a Loki label there, on purpose** — one label value per trace
would blow up the index. The jump to Tempo is a derived field on the datasource, which
regexes it out of the line at query time. Log → trace works; `{trace_id="..."}` does not.

## Grafana Dashboards

Two dashboards read these metrics.

**"FrontDesk AI — Agent Performance"** (uid `frontdeskai-agent-performance`) is the workshop one:
per-agent evaluation and tuning, filterable by participant namespace. Its JSON lives in the course
repo under `resources/`, not here — where the time goes, where the tokens go, how many ReAct
iterations each worker burned, and a per-agent scorecard. It leads with **means**, which are always
correct, and shows percentiles alongside them.

⚠️ `resources/frontdeskai-dashboard.json` in this repo plots "Response Latency (P50 / P95 / P99)".
Those panels were meaningless before the bucket fix and are correct after it — but only against a
pod running an image built from 2026-09-11 or later.

A dedicated dashboard **"Agentic AI Observability"** is also available in Grafana (`aiagentic-comp` folder).

**Panels (top to bottom):**

| Panel | Type | Data Source | Description |
|-------|------|------------|-------------|
| Avg Request Duration | Bar Gauge | Prometheus | Avg request + LLM call duration (instant query, `sum/count`) |
| Avg LLM Duration by Agent | Bar Gauge | Prometheus | Per-agent LLM latency breakdown |
| Requests by Category | Bar Gauge | Prometheus | Cumulative request count per category |
| Tokens by Agent | Bar Gauge | Prometheus | Cumulative token consumption per agent |
| Request Activity | Time Series | Prometheus | Category counter over time (cumulative) |
| Token Accumulation | Time Series | Prometheus | Token counter over time (cumulative) |
| Category Distribution | Pie Chart | Prometheus | Donut chart of request categories |
| Total Tokens / Requests / Escalations / Errors | Stat | Prometheus | Summary stat panels |
| All Application Logs | Logs | Loki | JSON logs filtered by `trace_id != "0"` |
| Recent Traces | Table | Loki | One row per trace with clickable Trace ID → Tempo Explore |

**Note**: The Recent Traces panel uses Loki (not Tempo TraceQL) due to a Grafana 12.x gRPC streaming limitation with Tempo's HTTP API. Trace IDs are clickable data links that open the full trace waterfall in Grafana Explore via Tempo.

## Verifying the Setup

### Check Traces in Tempo

```bash
curl -s "http://tempo.monitoring.svc.cluster.local:3200/api/search?tags=service.name%3Dfrontdeskai&limit=5"
```

### Check Metrics in Prometheus

```bash
curl -s "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090/api/v1/query" \
  --data-urlencode 'query={__name__=~"frontdeskai.*"}'
```

From outside the cluster, port-forward first (Prometheus has no NodePort):

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9091:9090
```

### Check Logs in Loki

```bash
curl -s "http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={app="frontdeskai"} | json' --data-urlencode 'limit=5'
```

### Send a Test Request

```bash
# One request against the NodePort-exposed app
bash scripts/generate-test-traffic.sh 1 0

# Or point it at a different host
FRONTDESKAI_URL=http://localhost:8000 bash scripts/generate-test-traffic.sh 5 10
```

The script logs in as `loadtest@test.com` and cycles through questions across all categories, so a
handful of requests is enough to populate every dashboard panel.

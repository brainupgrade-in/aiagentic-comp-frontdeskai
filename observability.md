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

## Span Hierarchy

Each chat request creates a parent span with child spans for each LLM agent call:

```
chat.send (parent)                    ← app.py
├── llm.supervisor                    ← agents.py (classification)
├── llm.<worker>                      ← agents.py (hr/tech/finance/facilities/general)
│   (hr_worker, tech_worker, etc.)
├── llm.manager                       ← agents.py (if escalated)
└── (all logs correlated via trace_id/span_id)
```

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
| `frontdeskai_escalations_total` | Counter | — | Requests escalated to manager |
| `frontdeskai_fallbacks_total` | Counter | — | Fallback template responses |
| `frontdeskai_agent_errors_total` | Counter | `agent` | Agent errors |
| `frontdeskai_request_duration_seconds` | Histogram | — | End-to-end `/chat/send` latency |

### PromQL Examples

```promql
# p95 request latency
histogram_quantile(0.95, sum(rate(frontdeskai_request_duration_seconds_bucket[5m])) by (le))

# LLM call duration by agent (p95)
histogram_quantile(0.95, sum(rate(frontdeskai_llm_call_duration_seconds_bucket[5m])) by (le, agent))

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
# requirements.txt
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
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://tempo.monitoring.svc.cluster.local:4317` | Tempo OTLP gRPC endpoint |

### Kubernetes Pod Annotations

For Prometheus to scrape the app, the deployment must have these annotations:

```yaml
spec:
  template:
    metadata:
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: "/metrics"
```

Add via kubectl:
```bash
kubectl patch deployment frontdeskai -n <NAMESPACE> --type=merge -p '{
  "spec": {"template": {"metadata": {"annotations": {
    "prometheus.io/scrape": "true",
    "prometheus.io/port": "8000",
    "prometheus.io/path": "/metrics"
  }}}}
}'
```

## Code Structure

| File | Observability Role |
|------|--------------------|
| `observability.py` | Central module: TracerProvider, MeterProvider, JsonFormatter, `trace_llm_call` context manager |
| `app.py` | Calls `init_observability()`, creates `chat.send` parent span, records request metrics, mounts `/metrics` |
| `agents.py` | Each agent function uses `trace_llm_call("agent_name")` to create child spans |

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

## Grafana Dashboard

A dedicated dashboard **"Distributed Tracing - FrontDesk AI"** is available in Grafana.

**Panels:**

| Panel | Data Source | Description |
|-------|------------|-------------|
| Trace Search | Tempo | Recent traces table with clickable trace IDs |
| Request Duration p50/p95/p99 | Prometheus | End-to-end latency percentiles |
| LLM Call Duration by Agent | Prometheus | Per-agent LLM latency (p95) |
| Request Rate | Prometheus | Requests per second by category |
| Token Usage by Agent | Prometheus | Token consumption rate by agent |
| Escalations & Errors | Prometheus | Escalation, fallback, and error rates |
| Category Distribution | Prometheus | Donut chart of request categories |
| Total Tokens / Requests / Escalations / Errors | Prometheus | Stat panels |
| Correlated Logs | Loki | JSON logs filtered by trace_id |

## Verifying the Setup

### Check Traces in Tempo

```bash
curl -s "http://tempo.monitoring.svc.cluster.local:3200/api/search?tags=service.name%3Dfrontdeskai&limit=5"
```

### Check Metrics in Prometheus

```bash
curl -s "http://prometheus-server.monitoring.svc.cluster.local:80/api/v1/query" \
  --data-urlencode 'query={__name__=~"frontdeskai.*"}'
```

### Check Logs in Loki

```bash
curl -s "http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={app="frontdeskai"} | json' --data-urlencode 'limit=5'
```

### Send a Test Request

```bash
kubectl exec -n <NAMESPACE> deployment/frontdeskai -- python3 -c "
import requests
s = requests.Session()
s.post('http://localhost:8000/login', data={'email': 'test@test.com', 'password': 'brainupgrade'})
r = s.post('http://localhost:8000/chat/send', data={'message': 'What is the leave policy?'})
print(r.status_code, r.text[:200])
"
```

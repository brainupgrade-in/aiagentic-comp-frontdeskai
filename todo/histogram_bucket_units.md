# Defect: latency histogram buckets are millisecond-scale, the values are seconds

**Found:** 2026-09-11, while building the agent-performance Grafana dashboard.
**Files:** `app/observability.py` (both histograms), `resources/frontdeskai-dashboard.json`
(the P50/P95/P99 panels built on them).

## What is wrong

```python
llm_call_duration = _meter.create_histogram(
    "frontdeskai_llm_call_duration_seconds", unit="s")   # no explicit boundaries
```

With no `View`, OpenTelemetry applies its **default** explicit bucket boundaries:

```
0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000
```

Those are chosen for **milliseconds**. The instrument records **seconds**. Measured on
`agenticaiu31`, every observation lands in the first real bucket:

| | measured |
|---|---|
| `..._count{agent="supervisor"}` | 12 |
| `..._sum{agent="supervisor"}` | 18.82 s (≈1.57 s per call) |
| `..._bucket{agent="supervisor", le="5.0"}` | **12** — i.e. all of them |
| every larger bucket | 12 |

End-to-end requests are slower (mean 13–32 s measured) so those do spread across
`le=25/50`, but per-agent latency — the number the whole dashboard is about — has no
resolution at all.

## Why it matters

`histogram_quantile()` interpolates **inside** the bucket it lands in. With every
observation in `(0, 5]`, p50, p95 and p99 all return roughly the same made-up value,
and it moves with the bucket edge rather than with the data. The existing
`resources/frontdeskai-dashboard.json` panel *"Response Latency (P50 / P95 / P99)"*
renders three lines that look plausible and are not measurements.

This is the failure Module 7 opens with: a number that survives review because nobody
asked what produced it.

## The fix

```python
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation

# Measured envelope on the lab gateway: per-agent calls 0.9-3.3 s,
# end-to-end requests 13-32 s. Boundaries chosen to give resolution across both.
_LATENCY_BUCKETS = [0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55]

meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[reader],
    views=[
        View(instrument_name="frontdeskai_llm_call_duration_seconds",
             aggregation=ExplicitBucketHistogramAggregation(_LATENCY_BUCKETS)),
        View(instrument_name="frontdeskai_request_duration_seconds",
             aggregation=ExplicitBucketHistogramAggregation(_LATENCY_BUCKETS)),
    ],
)
```

## What it unblocks, and what to do until then

- The agent-performance dashboard deliberately ships **means** (`_sum / _count`), which are
  correct today, and no percentile panels. Once this lands, add p50/p95 alongside the means —
  and only then is the deck's *"report a range"* actually available in the tooling.
- `resources/frontdeskai-dashboard.json`'s three percentile panels should be treated as
  wrong until the image is rebuilt.

Needs an image rebuild (arm64 leg ~12 min in Actions) and a redeploy per participant, so it
is not urgent — but it must not be quietly forgotten, because the panels do not look broken.

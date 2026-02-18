"""Centralized OpenTelemetry observability: metrics, tracing, structured logging."""

import json
import logging
import time
from contextlib import contextmanager

import os

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.trace import StatusCode
from prometheus_client import make_asgi_app


# ── JSON Log Formatter ──────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """Emit JSON log lines with trace_id and span_id correlation."""

    def format(self, record):
        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        trace_id = format(ctx.trace_id, '032x') if ctx and ctx.trace_id else "0"
        span_id = format(ctx.span_id, '016x') if ctx and ctx.span_id else "0"

        log = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": trace_id,
            "span_id": span_id,
        }
        # Attach extra fields (agent, category, etc.)
        for key in ("agent", "category", "employee", "duration_ms", "tokens"):
            val = getattr(record, key, None)
            if val is not None:
                log[key] = val

        if record.exc_info and record.exc_info[0]:
            log["exception"] = self.formatException(record.exc_info)

        return json.dumps(log)


# ── Module-level references (populated by init_observability) ────────

_tracer = None
_meter = None

# Metrics handles
llm_call_duration = None
llm_tokens_total = None
category_counter = None
escalation_counter = None
fallback_counter = None
agent_error_counter = None
request_duration = None

logger = logging.getLogger("frontdeskai")


def init_observability():
    """Initialize OTel tracing, Prometheus metrics, and JSON logging."""
    global _tracer, _meter
    global llm_call_duration, llm_tokens_total, category_counter
    global escalation_counter, fallback_counter, agent_error_counter, request_duration

    service_name = os.environ.get("OTEL_SERVICE_NAME", "frontdeskai")
    resource = Resource.create({"service.name": service_name})

    # Tracing with OTLP export to Tempo
    provider = TracerProvider(resource=resource)
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo.monitoring.svc.cluster.local:4317")
    otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("frontdeskai")

    # Metrics with Prometheus exporter
    reader = PrometheusMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)
    _meter = metrics.get_meter("frontdeskai")

    llm_call_duration = _meter.create_histogram(
        "frontdeskai_llm_call_duration_seconds",
        description="LLM call latency per agent",
        unit="s",
    )
    llm_tokens_total = _meter.create_counter(
        "frontdeskai_llm_tokens_total",
        description="Total LLM tokens consumed",
    )
    category_counter = _meter.create_counter(
        "frontdeskai_category_total",
        description="Requests by category",
    )
    escalation_counter = _meter.create_counter(
        "frontdeskai_escalations_total",
        description="Escalated requests",
    )
    fallback_counter = _meter.create_counter(
        "frontdeskai_fallbacks_total",
        description="Fallback template uses",
    )
    agent_error_counter = _meter.create_counter(
        "frontdeskai_agent_errors_total",
        description="Agent errors",
    )
    request_duration = _meter.create_histogram(
        "frontdeskai_request_duration_seconds",
        description="End-to-end /chat/send latency",
        unit="s",
    )

    # Structured JSON logging
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("frontdeskai")
    root.handlers.clear()
    root.addHandler(handler)
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    root.info("Observability initialized")


def get_tracer():
    return _tracer or trace.get_tracer("frontdeskai")


@contextmanager
def trace_llm_call(agent_name: str):
    """Context manager: creates a span, measures duration, yields a dict for token capture."""
    tracer = get_tracer()
    ctx = {"response": None}
    with tracer.start_as_current_span(f"llm.{agent_name}") as span:
        span.set_attribute("agent.name", agent_name)
        start = time.monotonic()
        try:
            yield ctx
            elapsed = time.monotonic() - start
            span.set_status(StatusCode.OK)

            # Record duration
            if llm_call_duration:
                llm_call_duration.record(elapsed, {"agent": agent_name})

            # Extract token usage from response metadata
            resp = ctx.get("response")
            if resp and hasattr(resp, "response_metadata"):
                meta = resp.response_metadata or {}
                usage = meta.get("token_usage") or meta.get("usage") or {}
                total_tokens = usage.get("total_tokens", 0)
                if total_tokens and llm_tokens_total:
                    llm_tokens_total.add(total_tokens, {"agent": agent_name})
                    span.set_attribute("llm.tokens", total_tokens)

            logger.info(
                "LLM call completed",
                extra={"agent": agent_name, "duration_ms": round(elapsed * 1000, 1)},
            )
        except Exception as e:
            elapsed = time.monotonic() - start
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            if agent_error_counter:
                agent_error_counter.add(1, {"agent": agent_name})
            logger.error(
                "LLM call failed",
                extra={"agent": agent_name, "duration_ms": round(elapsed * 1000, 1)},
                exc_info=True,
            )
            raise


def get_metrics_app():
    """Return a prometheus_client ASGI app for mounting at /metrics."""
    return make_asgi_app()

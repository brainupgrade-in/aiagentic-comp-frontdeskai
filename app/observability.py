"""Centralized OpenTelemetry observability: metrics, tracing, structured logging, Langfuse."""

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
from opentelemetry.sdk.metrics.view import (
    ExplicitBucketHistogramAggregation,
    View,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.trace import StatusCode
from prometheus_client import make_asgi_app

try:
    from langfuse.callback import CallbackHandler as LangfuseCallbackHandler
    _langfuse_available = True
except ImportError:
    LangfuseCallbackHandler = None  # type: ignore[assignment,misc]
    _langfuse_available = False


# ── Latency histogram buckets ───────────────────────────────────────

# Both latency histograms record SECONDS. Without an explicit aggregation
# OpenTelemetry applies its default boundaries -- 0, 5, 10, 25 ... 10000 --
# which are chosen for MILLISECONDS, so every real observation lands in the
# first bucket and histogram_quantile can only interpolate inside it: p50, p95
# and p99 then return roughly the same fabricated number. Measured before this
# fix on the lab gateway: supervisor, 12 calls totalling 18.82s, all 12 in
# le=5.0.
#
# Boundaries below cover the measured envelope -- per-agent calls 0.9-3.3s,
# end-to-end requests 13-57s -- with resolution across both.
_LATENCY_BUCKETS = [0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 55.0]

_LATENCY_VIEWS = [
    View(
        instrument_name=name,
        aggregation=ExplicitBucketHistogramAggregation(_LATENCY_BUCKETS),
    )
    for name in (
        "frontdeskai_llm_call_duration_seconds",
        "frontdeskai_request_duration_seconds",
    )
]


# ── JSON Log Formatter ──────────────────────────────────────────────

# Attributes every LogRecord carries — used to isolate caller-supplied extras.
_STD_LOGRECORD_ATTRS = set(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


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
        # Attach every extra= field (agent, category, langfuse_host, …) —
        # anything the caller passed that isn't a standard LogRecord attribute.
        for key, val in record.__dict__.items():
            if key not in _STD_LOGRECORD_ATTRS and key not in log and val is not None:
                log[key] = val

        if record.exc_info and record.exc_info[0]:
            log["exception"] = self.formatException(record.exc_info)

        return json.dumps(log, default=str)


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

# Langfuse
langfuse_enabled = False
_lf_handler = None

logger = logging.getLogger("frontdeskai")


def init_observability():
    """Initialize OTel tracing, Prometheus metrics, and JSON logging."""
    global _tracer, _meter, langfuse_enabled
    global llm_call_duration, llm_tokens_total, category_counter
    global escalation_counter, fallback_counter, agent_error_counter, request_duration

    service_name = os.environ.get("OTEL_SERVICE_NAME", "frontdeskai")
    resource = Resource.create({"service.name": service_name})

    # Tracing. Spans are still recorded locally when no collector is set; an
    # empty OTEL_EXPORTER_OTLP_ENDPOINT skips the exporter rather than retrying
    # against a host that is not there, which BatchSpanProcessor does silently.
    provider = TracerProvider(resource=resource)
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo.monitoring.svc.cluster.local:4317")
    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("frontdeskai")

    # Metrics with Prometheus exporter
    reader = PrometheusMetricReader()
    meter_provider = MeterProvider(
        resource=resource, metric_readers=[reader], views=_LATENCY_VIEWS
    )
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

    # Langfuse — enabled only when library is available and all three env vars are set
    lf_secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    lf_public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    lf_host = os.environ.get("LANGFUSE_HOST", "")
    if not _langfuse_available:
        root.info("Langfuse disabled (langfuse package not installed)")
    elif lf_secret and lf_public and lf_host:
        langfuse_enabled = True
        root.info("Langfuse enabled", extra={"langfuse_host": lf_host})
    else:
        root.info("Langfuse disabled (LANGFUSE_SECRET_KEY/PUBLIC_KEY/HOST not set)")

    root.info("Observability initialized")


def get_tracer():
    return _tracer or trace.get_tracer("frontdeskai")


class TokenCaptureHandler:
    """Capture token usage from a chain whose output is a parsed object.

    `with_structured_output()` returns the Pydantic model, not the AIMessage, so
    `response_metadata` -- where trace_llm_call normally reads token counts -- is
    gone by the time the call site sees the result. That silently attributed ZERO
    tokens to the supervisor and to every worker's final answer: two of the five
    workflow legs, invisible in cost reporting while their latency looked fine.

    A callback sees the raw LLMResult before parsing, so it works for structured
    and unstructured calls alike. Deliberately not a BaseCallbackHandler subclass:
    LangChain duck-types handlers, and this keeps observability.py free of a
    langchain import.
    """

    raise_error = False
    run_inline = False
    ignore_llm = False
    ignore_chain = True
    ignore_agent = True
    ignore_retriever = True
    ignore_chat_model = False
    ignore_retry = True
    ignore_custom_event = True

    def __init__(self):
        self.total_tokens = 0

    def on_llm_end(self, response, **kwargs):
        # Provider-level usage, which is where an OpenAI-compatible gateway puts it.
        usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
        total = usage.get("total_tokens") or 0
        if not total:
            # Per-generation usage_metadata, the newer LangChain shape.
            for gen_list in getattr(response, "generations", []) or []:
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    um = getattr(msg, "usage_metadata", None) or {}
                    total += um.get("total_tokens") or 0
        self.total_tokens += total

    def __getattr__(self, name):
        # Every other on_* callback LangChain may probe for.
        if name.startswith("on_"):
            return lambda *a, **kw: None
        raise AttributeError(name)


@contextmanager
def trace_llm_call(agent_name: str):
    """Context manager: creates a span, measures duration, yields a dict for token capture."""
    tracer = get_tracer()
    ctx = {"response": None, "token_handler": TokenCaptureHandler()}
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
            total_tokens = 0
            if resp is not None and hasattr(resp, "response_metadata"):
                meta = resp.response_metadata or {}
                usage = meta.get("token_usage") or meta.get("usage") or {}
                total_tokens = usage.get("total_tokens", 0) or 0
            if not total_tokens:
                # Structured-output chains hand back a parsed object with no
                # metadata; the callback saw the raw LLMResult on the way past.
                total_tokens = ctx["token_handler"].total_tokens
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


def get_langfuse_handler():
    """Return the process-wide Langfuse callback handler (None if disabled).

    One handler is shared by every request: in langfuse 2.x each
    CallbackHandler builds its own client with its own consumer threads and
    HTTP connection, so constructing one per chat leaked threads and left
    queued events with no one to flush them. Per-request identity travels in
    the RunnableConfig metadata instead — see `langfuse_metadata()`.
    """
    global _lf_handler
    if not langfuse_enabled or LangfuseCallbackHandler is None:
        return None
    if _lf_handler is None:
        _lf_handler = LangfuseCallbackHandler()
        # Log once whether the keys actually authenticate — otherwise a wrong
        # key or region silently produces zero traces.
        try:
            ok = _lf_handler.auth_check()
        except Exception as e:
            ok = f"error: {e}"
        logger.info(
            "Langfuse handler ready",
            extra={"langfuse_host": os.environ.get("LANGFUSE_HOST", ""), "auth_check": ok},
        )
    return _lf_handler


def langfuse_metadata(user_id: str = "", session_id: str = "") -> dict:
    """RunnableConfig metadata that tags a Langfuse trace with user + session."""
    return {"langfuse_user_id": user_id, "langfuse_session_id": session_id}


def flush_langfuse() -> None:
    """Send anything still queued — called on shutdown so a rollout doesn't drop traces."""
    if _lf_handler is None:
        return
    try:
        _lf_handler.langfuse.flush()
    except Exception:
        logger.warning("Langfuse flush failed", exc_info=True)


def get_metrics_app():
    """Return a prometheus_client ASGI app for mounting at /metrics."""
    return make_asgi_app()

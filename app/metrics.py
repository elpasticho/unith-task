"""Prometheus metrics for the event pipeline.

Exposed at GET /metrics (text/plain; version=0.0.4).
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["metrics"])

# ── Consumer ──────────────────────────────────────────────────────────────────
messages_received = Counter(
    "consumer_messages_received_total",
    "Total RabbitMQ messages received by the consumer",
)
messages_duplicate = Counter(
    "consumer_messages_duplicate_total",
    "Messages skipped because their message_id was already processed",
)
enrichment_errors = Counter(
    "consumer_enrichment_errors_total",
    "Enrichment failures by error type",
    labelnames=["error_type"],  # transient | fatal
)
enrichment_duration = Histogram(
    "consumer_enrichment_duration_seconds",
    "Time spent in the enrichment provider",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# ── Delivery worker ───────────────────────────────────────────────────────────
deliveries_attempted = Counter(
    "delivery_attempts_total",
    "Total HTTP delivery attempts",
)
deliveries_succeeded = Counter(
    "delivery_successes_total",
    "Delivery attempts that returned 2xx",
)
deliveries_failed = Counter(
    "delivery_failures_total",
    "Delivery attempts that did not return 2xx",
    labelnames=["http_status"],  # e.g. "503", "timeout"
)
deliveries_dead = Counter(
    "delivery_dead_total",
    "Deliveries moved to dead status after exhausting retries",
)
delivery_duration = Histogram(
    "delivery_http_duration_seconds",
    "Time spent waiting for the subscriber HTTP response",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

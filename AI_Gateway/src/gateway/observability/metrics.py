"""
Prometheus Metrics Export.
"""

import logging
from prometheus_client import Counter, Histogram, Gauge
from gateway.models.usage import UsageRecord

logger = logging.getLogger(__name__)


class MetricsExporter:
    """Manages Prometheus metrics for the AI Gateway."""

    def __init__(self):
        self.request_counter = Counter(
            "gateway_requests_total",
            "Total number of requests processed",
            ["provider", "model", "status"],
        )
        self.token_counter = Counter(
            "gateway_tokens_total",
            "Total number of tokens processed",
            ["provider", "model", "token_type"],
        )
        self.cost_counter = Counter(
            "gateway_cost_usd_total",
            "Total cost in USD",
            ["provider", "model", "project_id"],
        )
        self.cache_hits = Counter(
            "gateway_cache_hits_total",
            "Total number of exact match cache hits",
        )
        self.fallback_counter = Counter(
            "gateway_fallbacks_total",
            "Fallback hops taken",
            ["from_provider", "to_provider", "reason"],
        )
        self.rate_limit_counter = Counter(
            "gateway_rate_limit_hits_total",
            "Rate-limit events",
            ["scope"],
        )
        self.latency_histogram = Histogram(
            "gateway_request_latency_seconds",
            "Latency of requests to the gateway",
            ["provider", "model"],
            buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
        )
        self.active_requests = Gauge(
            "gateway_active_requests",
            "Number of requests currently being processed",
        )
        self.tokens_per_minute = Gauge(
            "gateway_tokens_per_minute",
            "Tokens in the current rolling minute",
            ["provider", "model"],
        )
        self.requests_per_minute = Gauge(
            "gateway_requests_per_minute",
            "Requests in the current rolling minute",
            ["provider", "model"],
        )
        logger.info("Initialized Prometheus metrics")

    def record_usage(self, record: UsageRecord) -> None:
        status = "success" if record.latency_ms > 0 else "error"

        self.request_counter.labels(
            provider=record.provider,
            model=record.model,
            status=status,
        ).inc()

        if record.cache_hit:
            self.cache_hits.inc()

        if record.total_tokens > 0:
            self.token_counter.labels(
                provider=record.provider, model=record.model, token_type="input"
            ).inc(record.input_tokens)
            self.token_counter.labels(
                provider=record.provider, model=record.model, token_type="output"
            ).inc(record.output_tokens)
            self.token_counter.labels(
                provider=record.provider, model=record.model, token_type="total"
            ).inc(record.total_tokens)

        if record.cost_usd > 0:
            self.cost_counter.labels(
                provider=record.provider,
                model=record.model,
                project_id=record.project_id,
            ).inc(record.cost_usd)

        if record.latency_ms > 0:
            self.latency_histogram.labels(
                provider=record.provider,
                model=record.model,
            ).observe(record.latency_ms / 1000.0)

    def set_live_window(self, provider: str, model: str, rpm: int, tpm: int) -> None:
        self.requests_per_minute.labels(provider=provider, model=model).set(rpm)
        self.tokens_per_minute.labels(provider=provider, model=model).set(tpm)

    def record_fallback(self, from_provider: str, to_provider: str, reason: str) -> None:
        self.fallback_counter.labels(
            from_provider=from_provider, to_provider=to_provider, reason=reason
        ).inc()

    def record_rate_limit(self, scope: str) -> None:
        self.rate_limit_counter.labels(scope=scope).inc()

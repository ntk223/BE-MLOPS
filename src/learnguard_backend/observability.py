"""M10 logging, metrics, and audit-report contracts."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class EventRecord:
    event_name: str
    occurred_at: datetime
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunSummary:
    run_id: str
    status: str
    duration_seconds: float | None = None
    data_quality: dict[str, Any] = field(default_factory=dict)
    model_metrics: dict[str, Any] = field(default_factory=dict)
    slice_metrics: dict[str, Any] = field(default_factory=dict)
    failure_reason: str | None = None


class StructuredLogger:
    def log_event(self, event: EventRecord) -> None:
        """TODO: emit structured logs while excluding sensitive data."""
        raise NotImplementedError


class MetricsCollector:
    def record_request(self, route: str, latency_ms: float, status_code: int) -> None:
        """TODO: record API latency and error metrics."""
        raise NotImplementedError

    def record_llm_call(self, latency_ms: float, cost_usd: float | None,
                        status: str) -> None:
        """TODO: record LLM latency, cost, and fallback/validation status."""
        raise NotImplementedError


def write_run_summary(summary: RunSummary, output_path: str) -> None:
    """TODO: persist a machine-readable summary for one pipeline run."""
    raise NotImplementedError

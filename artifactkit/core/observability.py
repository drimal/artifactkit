"""Observability primitives shared by ArtifactService.

Two things a caller running this in production actually needs:
1. Structured log lines it can ship to whatever log pipeline it already
   has (CloudWatch, Datadog, plain stdout JSON), correlated by an
   operation_id that also comes back on the result.
2. A place to emit counters/timers without artifactkit taking a hard
   dependency on any specific metrics backend.

Metrics are opt-in via the MetricsSink protocol. The default is a
no-op so importing/using artifactkit never requires a metrics library
or produces unwanted output. Logging always happens (it's stdlib
`logging`, callers control visibility via handler/level config same as
any other library) -- metrics are the layer that requires opting in
because, unlike log lines, a badly configured metrics sink can throw
or block, and that must never be able to break an artifact delivery.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from typing import Protocol, runtime_checkable

logger = logging.getLogger("artifactkit")


def new_operation_id() -> str:
    """Short correlation id for one create()/create_files() call. Not a
    UUID in full: 12 hex chars is enough to correlate log lines and
    results within a service's log retention window without bloating
    every log line and result object."""
    return uuid.uuid4().hex[:12]


@runtime_checkable
class MetricsSink(Protocol):
    """Implement this to route artifactkit's counters and timers to a
    real metrics backend (statsd, CloudWatch EMF, Prometheus pushgateway,
    OpenTelemetry, whatever). Tags are a flat string->string dict --
    translate to your backend's label/dimension format in your
    implementation."""

    def increment(self, name: str, tags: dict[str, str] | None = None) -> None:
        """Increment a counter by 1."""
        ...

    def timing(self, name: str, duration_ms: float, tags: dict[str, str] | None = None) -> None:
        """Record a duration in milliseconds."""
        ...


class NoOpMetricsSink:
    """Default sink: every call is a no-op. Used when the caller hasn't
    wired up a metrics backend, so ArtifactService always has something
    to call without branching on "is metrics configured" everywhere."""

    def increment(self, name: str, tags: dict[str, str] | None = None) -> None:
        pass

    def timing(self, name: str, duration_ms: float, tags: dict[str, str] | None = None) -> None:
        pass


class LoggingMetricsSink:
    """Convenience sink that logs metrics as structured debug lines
    instead of sending them anywhere. Useful for local development or
    as a stopgap before a real metrics backend is wired in -- NOT
    a substitute for one in production, since log-mining for metrics
    doesn't give you dashboards, alerting, or aggregation."""

    def increment(self, name: str, tags: dict[str, str] | None = None) -> None:
        logger.debug("metric.increment", extra={"metric_name": name, "metric_tags": tags or {}})

    def timing(self, name: str, duration_ms: float, tags: dict[str, str] | None = None) -> None:
        logger.debug(
            "metric.timing",
            extra={"metric_name": name, "duration_ms": duration_ms, "metric_tags": tags or {}},
        )


@contextmanager
def _timed():
    """Yields a callable that returns elapsed milliseconds so far.
    Read it after the `with` block for the final duration."""
    start = time.monotonic()
    elapsed = lambda: (time.monotonic() - start) * 1000  # noqa: E731
    yield elapsed

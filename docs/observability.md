# Observability

Every call to `create()` or `create_files()` is logged and, if you
supply a [`MetricsSink`][artifactkit.MetricsSink], measured. Both are
correlated by an `operation_id` generated once per call and returned
on the result object, so you can take a result your code is holding
and find exactly the log lines that produced it.

## Logging

All logging goes through the standard library logger named
`"artifactkit"`. Configure it the same way you'd configure any other
library's logger — set a handler, a level, a formatter — artifactkit
never calls `logging.basicConfig()` itself, so it won't fight with
whatever logging setup your application already has.

```python
import logging
logging.getLogger("artifactkit").setLevel(logging.INFO)
```

### Log events

| Event | Level | Emitted when |
|---|---|---|
| `artifact.create.start` | INFO | A `create()` call begins |
| `artifact.create.success` | INFO | `create()` completes successfully |
| `artifact.create.failed` | ERROR | `create()` raises, for any reason |
| `artifact.validate.failed` | ERROR | A backend's structural validation fails (subset of the above — logged before the exception is raised) |
| `artifact.create_files.start` | INFO | A `create_files()` call begins |
| `artifact.create_files.success` | INFO | `create_files()` completes successfully |
| `artifact.create_files.failed` | ERROR | `create_files()` raises, for any reason (including a `source_path` that doesn't exist, checked before any write starts) |
| `artifact.write.retry` | WARNING | The write step is retried after a transient failure |

### Fields on every record

Every event above carries `op_id` (the operation id) plus context
specific to that call — the exact set depends on the event, but
commonly includes:

- `artifact_filename` — the resolved filename (not `filename`, which
  collides with a reserved attribute on Python's `LogRecord`)
- `format` — the `ArtifactFormat` value, for `create()` calls
- `destination` — the destination class name (`LocalDirectoryDestination`,
  `S3Destination`, or your own)
- `duration_ms` — on success events, total call duration
- `write_attempts` — on `create.success`, how many write attempts it took
- `attempt`, `error` — on `write.retry`, which attempt and what failed
- `errors` — on `validate.failed`, the backend's structural error list

!!! note
    The exact field set is not a frozen public contract the way the
    Python API is — if you're building alerting on log content rather
    than metrics, prefer matching on the event name and `op_id`, and
    treat additional fields as informational.

## Metrics

Metrics are opt-in. The default [`NoOpMetricsSink`][artifactkit.NoOpMetricsSink]
means `ArtifactService` never requires a metrics backend to function —
implement [`MetricsSink`][artifactkit.MetricsSink] to route counters and
timers to whatever you actually run (statsd, CloudWatch EMF, Prometheus
pushgateway, OpenTelemetry):

```python
from artifactkit import ArtifactService, MetricsSink

class DatadogMetrics:
    def increment(self, name: str, tags: dict[str, str] | None = None) -> None:
        statsd.increment(name, tags=[f"{k}:{v}" for k, v in (tags or {}).items()])

    def timing(self, name: str, duration_ms: float, tags: dict[str, str] | None = None) -> None:
        statsd.timing(name, duration_ms, tags=[f"{k}:{v}" for k, v in (tags or {}).items()])

service = ArtifactService(metrics=DatadogMetrics())
```

[`LoggingMetricsSink`][artifactkit.LoggingMetricsSink] ships as a
convenience for local development — it logs each metric as a DEBUG
line instead of sending it anywhere. It is **not** a substitute for a
real metrics backend in production: log-mining for metrics gives you
none of the dashboarding, alerting, or aggregation a real sink does.

### Counters

| Name | Tags | Meaning |
|---|---|---|
| `artifactkit.create.success` | `format`, `destination` | A `create()` call succeeded |
| `artifactkit.create.failure` | `format`, `destination`, `error_type` | A `create()` call raised |
| `artifactkit.validate.failure` | `format`, `destination` | Structural validation failed (fires alongside `create.failure`) |
| `artifactkit.create_files.success` | `destination`, `bundled` | A `create_files()` call succeeded |
| `artifactkit.create_files.failure` | `destination`, `bundled`, `error_type` | A `create_files()` call raised |
| `artifactkit.write.retry` | `format`/`bundled`, `destination`, `attempt` | One retry attempt on the write step |
| `artifactkit.write.verify_failed` | `format`/`bundled`, `destination` | A write "succeeded" but failed verification |

### Timers (milliseconds)

| Name | Meaning |
|---|---|
| `artifactkit.render.duration_ms` | Time spent in the backend's `render()` (create() only) |
| `artifactkit.write.duration_ms` | Time spent in one write+verify attempt |
| `artifactkit.create.duration_ms` | Total `create()` call duration |
| `artifactkit.create_files.duration_ms` | Total `create_files()` call duration |

## Correlating results with logs

```python
result = service.create(spec, ArtifactFormat.DOCX, destination)
logger.info("delivered artifact", extra={"artifactkit_op_id": result.operation_id})
```

Searching your log pipeline for `result.operation_id` returns every
line artifactkit itself emitted for that specific call — the start
event, any retries, and the success or failure event — regardless of
how many other calls ran concurrently.

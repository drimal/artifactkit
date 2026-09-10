import logging

import pytest

from artifactkit import (
    ArtifactFormat,
    ArtifactService,
    DocumentSpec,
    FileBundleSpec,
    Paragraph,
    RawFile,
    destination_from_uri,
)
from artifactkit.core.backend import ValidationResult
from artifactkit.core.errors import ArtifactError, ArtifactValidationError
from artifactkit.core.observability import MetricsSink, NoOpMetricsSink


class RecordingMetrics:
    """A MetricsSink that just remembers every call, so tests can assert
    on what was actually emitted rather than trusting it silently."""

    def __init__(self):
        self.increments: list[tuple[str, dict]] = []
        self.timings: list[tuple[str, float, dict]] = []

    def increment(self, name, tags=None):
        self.increments.append((name, tags or {}))

    def timing(self, name, duration_ms, tags=None):
        self.timings.append((name, duration_ms, tags or {}))


@pytest.fixture
def destination(tmp_path):
    return destination_from_uri(str(tmp_path))


@pytest.fixture
def spec():
    return DocumentSpec(title="Obs test", blocks=(Paragraph.of("x"),))


def test_noop_metrics_sink_satisfies_protocol():
    sink = NoOpMetricsSink()
    assert isinstance(sink, MetricsSink)
    sink.increment("x")
    sink.timing("y", 1.0)  # must not raise


def test_create_result_carries_operation_id(spec, destination):
    service = ArtifactService()
    result = service.create(spec, ArtifactFormat.DOCX, destination, filename="a.docx")
    assert result.operation_id
    assert len(result.operation_id) == 12


def test_two_calls_get_different_operation_ids(spec, destination):
    service = ArtifactService()
    r1 = service.create(spec, ArtifactFormat.DOCX, destination, filename="a.docx")
    r2 = service.create(spec, ArtifactFormat.DOCX, destination, filename="b.docx")
    assert r1.operation_id != r2.operation_id


def test_create_files_result_carries_operation_id(destination):
    service = ArtifactService()
    bundle = FileBundleSpec(files=(RawFile("x.txt", content=b"1"),))
    result = service.create_files(bundle, destination)
    assert result.operation_id
    assert len(result.operation_id) == 12


def test_metrics_emitted_on_create_success(spec, destination):
    metrics = RecordingMetrics()
    service = ArtifactService(metrics=metrics)
    service.create(spec, ArtifactFormat.DOCX, destination, filename="a.docx")

    names = [name for name, _ in metrics.increments]
    assert "artifactkit.create.success" in names
    timing_names = [name for name, _, _ in metrics.timings]
    assert "artifactkit.render.duration_ms" in timing_names
    assert "artifactkit.write.duration_ms" in timing_names
    assert "artifactkit.create.duration_ms" in timing_names


def test_metrics_emitted_on_validation_failure(spec, destination):
    from artifactkit.core.registry import BackendRegistry

    class AlwaysInvalidBackend:
        def render(self, spec, path):
            path.write_bytes(b"x")

        def validate(self, path, spec):
            return ValidationResult(is_valid=False, errors=("broken",))

    registry = BackendRegistry()
    registry.register(ArtifactFormat.DOCX, AlwaysInvalidBackend())
    metrics = RecordingMetrics()
    service = ArtifactService(registry=registry, metrics=metrics)

    with pytest.raises(ArtifactValidationError):
        service.create(spec, ArtifactFormat.DOCX, destination, filename="a.docx")

    names = [name for name, _ in metrics.increments]
    assert "artifactkit.validate.failure" in names
    assert "artifactkit.create.failure" in names
    # success metric must NOT fire on a failed call
    assert "artifactkit.create.success" not in names


def test_metrics_emitted_on_create_files_failure(destination, tmp_path):
    metrics = RecordingMetrics()
    service = ArtifactService(metrics=metrics)
    bundle = FileBundleSpec(files=(
        RawFile("missing.txt", source_path=str(tmp_path / "does_not_exist.txt")),
    ))

    with pytest.raises(ArtifactError):
        service.create_files(bundle, destination)

    names = [name for name, _ in metrics.increments]
    assert "artifactkit.create_files.failure" in names
    assert "artifactkit.create_files.success" not in names


def test_retry_emits_retry_metric_with_attempt_tag(spec, destination, tmp_path):
    from artifactkit.core.destinations import WriteResult

    class FlakyOnce:
        def __init__(self):
            self.n = 0

        def write(self, local_path, filename):
            self.n += 1
            if self.n == 1:
                raise ConnectionError("blip")
            target = tmp_path / filename
            target.write_bytes(local_path.read_bytes())
            return WriteResult(location=str(target), key=str(target))

        def verify(self, write_result, local_path):
            return True

        def is_retryable(self, exc):
            return isinstance(exc, ConnectionError)

        def presign(self, key):
            return None

    metrics = RecordingMetrics()
    service = ArtifactService(metrics=metrics)
    result = service.create(spec, ArtifactFormat.DOCX, FlakyOnce(), filename="r.docx")

    assert result.write_attempts == 2
    retry_calls = [tags for name, tags in metrics.increments if name == "artifactkit.write.retry"]
    assert len(retry_calls) == 1
    assert retry_calls[0]["attempt"] == "1"


def test_start_log_emitted_for_create(spec, destination, caplog):
    caplog.set_level(logging.INFO, logger="artifactkit")
    service = ArtifactService()
    service.create(spec, ArtifactFormat.DOCX, destination, filename="a.docx")
    messages = [r.message for r in caplog.records]
    assert "artifact.create.start" in messages
    assert "artifact.create.success" in messages


def test_log_records_carry_matching_operation_id(spec, destination, caplog):
    caplog.set_level(logging.INFO, logger="artifactkit")
    service = ArtifactService()
    result = service.create(spec, ArtifactFormat.DOCX, destination, filename="a.docx")

    op_ids_seen = {
        r.op_id for r in caplog.records if hasattr(r, "op_id") and r.name == "artifactkit"
    }
    assert op_ids_seen == {result.operation_id}


def test_create_files_failure_logs_before_raising(destination, tmp_path, caplog):
    """Regression test: an earlier version of this code ran the
    source_path fail-fast check BEFORE the start log and outside the
    try/except that emits the failure log, so a missing source_path
    failed with zero log lines. That must not happen."""
    caplog.set_level(logging.INFO, logger="artifactkit")
    service = ArtifactService()
    bundle = FileBundleSpec(files=(
        RawFile("missing.txt", source_path=str(tmp_path / "nope.txt")),
    ))

    with pytest.raises(ArtifactError):
        service.create_files(bundle, destination)

    messages = [r.message for r in caplog.records]
    assert "artifact.create_files.start" in messages
    assert "artifact.create_files.failed" in messages


def test_logging_metrics_sink_logs_as_debug(caplog):
    from artifactkit import LoggingMetricsSink

    caplog.set_level(logging.DEBUG, logger="artifactkit")
    sink = LoggingMetricsSink()
    sink.increment("test.counter", {"a": "b"})
    sink.timing("test.timer", 12.5, {"a": "b"})

    records = [r for r in caplog.records if r.name == "artifactkit"]
    assert any(r.message == "metric.increment" for r in records)
    assert any(r.message == "metric.timing" for r in records)

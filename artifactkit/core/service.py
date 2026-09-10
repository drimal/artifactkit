"""The one entry point every consumer (Strands tool, MCP tool, or plain
Python caller) goes through.

Two delivery paths, sharing the write/verify/retry/presign machinery:
- create(): structured content (docx/pptx/xlsx/pdf) through render ->
  validate -> write -> verify. Backends own render/validate.
- create_files(): arbitrary already-formed files (code, images, data,
  anything) straight to write -> verify, no render/validate step since
  there is no format-specific structure to check. Bundles into a
  single .zip automatically once the file count passes zip_threshold,
  so a multi-file delivery doesn't turn into N separate network round
  trips and N retry budgets.

See core/errors.py for why validation and write failures are raised
as distinct exception types, and core/observability.py for the
logging/metrics model.

Every call gets an operation_id: a short id generated once per
create()/create_files() call, attached to every log line for that
call and returned on the result. Correlate a result the caller has in
hand with the library's own logs by that id -- no need to reconstruct
which call produced which log lines from timestamps.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from artifactkit.core.destinations import OutputDestination, WriteResult
from artifactkit.core.errors import ArtifactError, ArtifactValidationError, ArtifactWriteError
from artifactkit.core.models import ArtifactFormat, ArtifactSpec, FileBundleSpec, RawFile
from artifactkit.core.observability import MetricsSink, NoOpMetricsSink, _timed, new_operation_id
from artifactkit.core.registry import BackendRegistry, default_registry
from artifactkit.core.retry import RetryPolicy, retry_with_backoff

logger = logging.getLogger("artifactkit")

_DEFAULT_ZIP_THRESHOLD = 5
_DEFAULT_BUNDLE_NAME = "artifacts"


@dataclass(frozen=True)
class ArtifactResult:
    location: str
    format: ArtifactFormat
    size_bytes: int
    checks_passed: tuple[str, ...]
    write_attempts: int = 1
    presigned_url: str | None = None
    presigned_expires_at: datetime | None = None
    operation_id: str = ""


@dataclass(frozen=True)
class FileDeliveryResult:
    """Result of create_files(). locations has one entry if the files
    were bundled into a zip, one entry per file otherwise."""

    locations: tuple[str, ...]
    bundled: bool
    file_count: int
    total_size_bytes: int
    write_attempts: int
    presigned_url: str | None = None
    presigned_expires_at: datetime | None = None
    operation_id: str = ""


class ArtifactService:
    """The single entry point for producing and delivering artifacts.

    Two methods, two different content shapes:
      - create(): a structured spec (DocumentSpec/PresentationSpec/
        WorkbookSpec) that a backend renders, validates, then writes
        and verifies at a destination.
      - create_files(): a FileBundleSpec of already-formed files
        (code, images, anything) delivered straight to write+verify,
        auto-zipped above a file-count threshold.

    Every call is logged with a correlating operation_id (also
    returned on the result) and, if a MetricsSink is supplied, emits
    counters and timers for render/write/create duration and outcome.
    See core/observability.py.

    Args:
        registry: Backend lookup for create(). Defaults to
            default_registry() (docx/pptx/xlsx/pdf).
        default_retry_policy: Retry behavior for the write+verify step
            when a call doesn't override it. Defaults to RetryPolicy().
        metrics: Where counters/timers go. Defaults to a no-op sink, so
            using ArtifactService never requires a metrics backend.
    """

    def __init__(
        self,
        registry: BackendRegistry | None = None,
        default_retry_policy: RetryPolicy | None = None,
        metrics: MetricsSink | None = None,
    ):
        self._registry = registry or default_registry()
        self._default_retry_policy = default_retry_policy or RetryPolicy()
        self._metrics = metrics or NoOpMetricsSink()

    def create(
        self,
        spec: ArtifactSpec,
        output_format: ArtifactFormat,
        destination: OutputDestination,
        *,
        filename: str | None = None,
        include_presigned_url: bool = False,
        retry_policy: RetryPolicy | None = None,
    ) -> ArtifactResult:
        """filename is optional: if omitted, spec.base_filename (when set)
        is adapted to output_format's extension. An explicit filename
        always wins over the spec's suggestion, since the same spec may
        legitimately be rendered under different names."""
        op_id = new_operation_id()
        policy = retry_policy or self._default_retry_policy
        backend = self._registry.get(output_format)
        resolved_filename = self._resolve_filename(spec, filename, output_format)
        tags = {"format": output_format.value, "destination": type(destination).__name__}

        logger.info(
            "artifact.create.start",
            extra={"op_id": op_id, "artifact_filename": resolved_filename, **tags},
        )

        with _timed() as total_elapsed:
            try:
                with self._temp_workspace() as tmp_dir:
                    local_path = tmp_dir / resolved_filename

                    # 1. Render: deterministic, never retried. A bad spec
                    #    fails the same way every time.
                    with _timed() as render_elapsed:
                        backend.render(spec, local_path)
                    self._metrics.timing("artifactkit.render.duration_ms", render_elapsed(), tags)

                    # 2. Validate: content/structure check, also never retried.
                    render_check = backend.validate(local_path, spec)
                    if not render_check.is_valid:
                        self._metrics.increment("artifactkit.validate.failure", tags)
                        logger.error(
                            "artifact.validate.failed",
                            extra={"op_id": op_id, "errors": render_check.errors, **tags},
                        )
                        raise ArtifactValidationError(
                            f"{output_format.value} failed validation: "
                            f"{'; '.join(render_check.errors)}"
                        )

                    # 3. Write + verify, retried together.
                    write_result, attempts = self._write_verified(
                        local_path, resolved_filename, destination, policy, op_id, tags
                    )

                    # 4. Presign is best-effort and never retried.
                    presigned_url, expires_at = self._maybe_presign(
                        destination, write_result, include_presigned_url
                    )

                    result = ArtifactResult(
                        location=write_result.location,
                        format=output_format,
                        size_bytes=local_path.stat().st_size,
                        checks_passed=render_check.checks_passed,
                        write_attempts=attempts,
                        presigned_url=presigned_url,
                        presigned_expires_at=expires_at,
                        operation_id=op_id,
                    )
            except Exception as exc:
                self._metrics.increment(
                    "artifactkit.create.failure", {**tags, "error_type": type(exc).__name__}
                )
                logger.error(
                    "artifact.create.failed",
                    extra={"op_id": op_id, "error": str(exc), **tags},
                )
                raise

        self._metrics.increment("artifactkit.create.success", tags)
        self._metrics.timing("artifactkit.create.duration_ms", total_elapsed(), tags)
        logger.info(
            "artifact.create.success",
            extra={
                "op_id": op_id,
                "location": result.location,
                "write_attempts": result.write_attempts,
                "duration_ms": round(total_elapsed(), 1),
                **tags,
            },
        )
        return result

    def create_files(
        self,
        spec: FileBundleSpec,
        destination: OutputDestination,
        *,
        zip_threshold: int = _DEFAULT_ZIP_THRESHOLD,
        include_presigned_url: bool = False,
        retry_policy: RetryPolicy | None = None,
    ) -> FileDeliveryResult:
        """Delivers an arbitrary set of files, code, images, data,
        anything the caller already has as bytes, to a destination.
        Skips render/validate entirely: the content is already formed,
        there is nothing format-specific to check beyond the write
        itself landing intact.

        If len(spec.files) > zip_threshold, all files are bundled into
        one .zip and delivered as a single artifact instead of one
        write per file. Below the threshold, each file is written and
        verified individually and every location is returned."""
        op_id = new_operation_id()
        policy = retry_policy or self._default_retry_policy
        will_bundle = len(spec.files) > zip_threshold
        tags = {
            "destination": type(destination).__name__,
            "bundled": str(will_bundle).lower(),
        }

        logger.info(
            "artifact.create_files.start",
            extra={"op_id": op_id, "file_count": len(spec.files), **tags},
        )

        with _timed() as total_elapsed:
            try:
                self._validate_source_paths(spec.files)
                with self._temp_workspace() as tmp_dir:
                    if will_bundle:
                        result = self._deliver_as_zip(
                            spec, destination, tmp_dir, policy, include_presigned_url, op_id, tags
                        )
                    else:
                        result = self._deliver_individually(
                            spec, destination, tmp_dir, policy, include_presigned_url, op_id, tags
                        )
            except Exception as exc:
                self._metrics.increment(
                    "artifactkit.create_files.failure", {**tags, "error_type": type(exc).__name__}
                )
                logger.error(
                    "artifact.create_files.failed",
                    extra={"op_id": op_id, "error": str(exc), **tags},
                )
                raise

        self._metrics.increment("artifactkit.create_files.success", tags)
        self._metrics.timing("artifactkit.create_files.duration_ms", total_elapsed(), tags)
        logger.info(
            "artifact.create_files.success",
            extra={
                "op_id": op_id,
                "locations": result.locations,
                "file_count": result.file_count,
                "duration_ms": round(total_elapsed(), 1),
                **tags,
            },
        )
        return result

    def _deliver_as_zip(
        self,
        spec: FileBundleSpec,
        destination: OutputDestination,
        tmp_dir: Path,
        policy: RetryPolicy,
        include_presigned_url: bool,
        op_id: str,
        tags: dict[str, str],
    ) -> FileDeliveryResult:
        zip_filename = f"{spec.bundle_filename or _DEFAULT_BUNDLE_NAME}.zip"
        zip_path = tmp_dir / zip_filename
        self._write_zip(spec.files, zip_path)

        write_result, attempts = self._write_verified(
            zip_path, zip_filename, destination, policy, op_id, tags
        )
        presigned_url, expires_at = self._maybe_presign(destination, write_result, include_presigned_url)

        return FileDeliveryResult(
            locations=(write_result.location,),
            bundled=True,
            file_count=len(spec.files),
            total_size_bytes=zip_path.stat().st_size,
            write_attempts=attempts,
            presigned_url=presigned_url,
            presigned_expires_at=expires_at,
            operation_id=op_id,
        )

    def _deliver_individually(
        self,
        spec: FileBundleSpec,
        destination: OutputDestination,
        tmp_dir: Path,
        policy: RetryPolicy,
        include_presigned_url: bool,
        op_id: str,
        tags: dict[str, str],
    ) -> FileDeliveryResult:
        locations: list[str] = []
        total_bytes = 0
        total_attempts = 0
        last_write_result: WriteResult | None = None

        for raw_file in spec.files:
            local_path = tmp_dir / raw_file.filename
            self._materialize(raw_file, local_path)
            write_result, attempts = self._write_verified(
                local_path, raw_file.filename, destination, policy, op_id, tags
            )
            locations.append(write_result.location)
            total_bytes += local_path.stat().st_size
            total_attempts += attempts
            last_write_result = write_result

        # Presigning N files would mean N URLs; only worth doing
        # automatically for the single-file case. A caller delivering
        # several files and wanting links for each should request
        # them per file.
        presigned_url, expires_at = None, None
        if include_presigned_url and len(spec.files) == 1 and last_write_result is not None:
            presigned_url, expires_at = self._maybe_presign(
                destination, last_write_result, include_presigned_url
            )

        return FileDeliveryResult(
            locations=tuple(locations),
            bundled=False,
            file_count=len(spec.files),
            total_size_bytes=total_bytes,
            write_attempts=total_attempts,
            presigned_url=presigned_url,
            presigned_expires_at=expires_at,
            operation_id=op_id,
        )

    def _write_zip(self, files: tuple[RawFile, ...], zip_path: Path) -> None:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for raw_file in files:
                if raw_file.source_path is not None:
                    archive.write(raw_file.source_path, arcname=raw_file.filename)
                else:
                    archive.writestr(raw_file.filename, raw_file.content)

    def _materialize(self, raw_file: RawFile, local_path: Path) -> None:
        """Writes a RawFile's content to local_path, reading from
        source_path if that's what was given rather than in-memory
        bytes. This is the point where source_path is actually read,
        deliberately as late as possible."""
        if raw_file.source_path is not None:
            shutil.copyfile(raw_file.source_path, local_path)
        else:
            local_path.write_bytes(raw_file.content)

    def _validate_source_paths(self, files: tuple[RawFile, ...]) -> None:
        """Fail fast, before any writing starts, if a source_path is
        missing or not a file -- a clear error up front beats a
        confusing FileNotFoundError partway through a zip or a batch
        of individual writes."""
        for raw_file in files:
            if raw_file.source_path is not None and not Path(raw_file.source_path).is_file():
                raise ArtifactError(
                    f"source_path does not exist or is not a file: "
                    f"{raw_file.source_path!r} (filename={raw_file.filename!r})"
                )

    def _write_verified(
        self,
        local_path: Path,
        filename: str,
        destination: OutputDestination,
        policy: RetryPolicy,
        op_id: str,
        tags: dict[str, str],
    ) -> tuple[WriteResult, int]:
        """Write + verify, retried together as one unit: a partial
        write should be redone from scratch, not verified in place.
        Shared by create() and create_files() so retry/verify logic
        exists in exactly one place."""
        attempts = 0

        def _write_and_verify() -> WriteResult:
            nonlocal attempts
            attempts += 1
            with _timed() as write_elapsed:
                result = destination.write(local_path, filename)
                verified = destination.verify(result, local_path)
            self._metrics.timing("artifactkit.write.duration_ms", write_elapsed(), tags)
            if not verified:
                self._metrics.increment("artifactkit.write.verify_failed", tags)
                raise ArtifactWriteError(f"write to {result.location} could not be verified")
            return result

        def _on_retry(attempt: int, exc: Exception) -> None:
            self._metrics.increment("artifactkit.write.retry", {**tags, "attempt": str(attempt)})
            logger.warning(
                "artifact.write.retry",
                extra={
                    "op_id": op_id,
                    "artifact_filename": filename,
                    "attempt": attempt,
                    "error": str(exc),
                    **tags,
                },
            )

        write_result = retry_with_backoff(
            _write_and_verify,
            policy,
            is_retryable=lambda exc: (
                not isinstance(exc, ArtifactWriteError) and destination.is_retryable(exc)
            ),
            on_retry=_on_retry,
        )
        return write_result, attempts

    def _maybe_presign(
        self,
        destination: OutputDestination,
        write_result: WriteResult,
        include_presigned_url: bool,
    ) -> tuple[str | None, datetime | None]:
        if not include_presigned_url:
            return None, None
        presigned = destination.presign(write_result.key)
        if presigned is None:
            return None, None
        return presigned

    def _resolve_filename(
        self, spec: ArtifactSpec, explicit_filename: str | None, output_format: ArtifactFormat
    ) -> str:
        if explicit_filename:
            return explicit_filename
        base = getattr(spec, "base_filename", None)
        if base:
            return f"{base}.{output_format.value}"
        raise ArtifactError(
            "no filename available: pass filename explicitly, "
            "or set base_filename on the spec"
        )

    @contextmanager
    def _temp_workspace(self):
        with tempfile.TemporaryDirectory(prefix="artifactkit_") as tmp:
            yield Path(tmp)

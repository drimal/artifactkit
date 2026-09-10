"""Backend contract every format implementation must satisfy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from artifactkit.core.models import ArtifactSpec


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    checks_passed: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


class ArtifactBackend(Protocol):
    """One backend per ArtifactFormat. Render is expected to raise on
    hard failure (bad input it can't recover from); validate is expected
    to return a result rather than raise, since a validation failure is
    an expected outcome the service needs to inspect, not a control-flow
    exception."""

    def render(self, spec: ArtifactSpec, output_path: Path) -> None:
        """Write the artifact to output_path. output_path's parent
        directory is guaranteed to exist; the file itself does not."""
        ...

    def validate(self, output_path: Path, spec: ArtifactSpec) -> ValidationResult:
        """Open the rendered file and check it is well-formed and
        roughly matches the spec (cheap structural checks, not a deep
        content diff)."""
        ...

"""Typed exceptions for artifactkit.

Kept as a small, flat hierarchy on purpose: a calling agent needs to
decide *what to do next* from the exception type alone, so each type
maps to a distinct remediation path rather than a generic failure.
"""

from __future__ import annotations


class ArtifactError(Exception):
    """Base class for all artifactkit errors. Catch this to catch anything
    the harness can raise; catch a subtype to handle one failure mode."""


class ArtifactValidationError(ArtifactError):
    """Raised when a rendered file fails backend validation (malformed
    structure, content mismatch against the spec). Remediation: fix the
    spec, not retry the same call."""


class ArtifactWriteError(ArtifactError):
    """Raised when a write to the destination could not be verified,
    after retries are exhausted. Remediation: check destination
    credentials/capacity, or retry later; the rendered file itself was
    valid."""


class UnsupportedFormatError(ArtifactError):
    """Raised when no backend is registered for the requested
    ArtifactFormat."""


class DestinationError(ArtifactError):
    """Raised for destination configuration problems that are not
    retryable (bad URI scheme, missing bucket, invalid local path)."""

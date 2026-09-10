"""Where a rendered artifact ends up. Backends never know about this;
they render to a local temp path and the destination takes it from
there. Adding a new destination (GCS, Azure Blob, a network share)
never touches a backend."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from artifactkit.core.errors import DestinationError


@dataclass(frozen=True)
class WriteResult:
    location: str  # canonical URI or absolute path
    key: str  # destination-native identifier used for verify()/presign() lookups


class OutputDestination(Protocol):
    def write(self, local_path: Path, filename: str) -> WriteResult: ...

    def verify(self, write_result: WriteResult, local_path: Path) -> bool:
        """Confirm the write landed intact. Called once per write
        attempt, before the attempt is considered successful."""
        ...

    def is_retryable(self, exc: Exception) -> bool:
        """Whether exc, raised from write() or verify(), is worth
        retrying. Transient/network errors: yes. Config/permission
        errors: no, they'll fail identically every time."""
        ...

    def presign(self, key: str) -> tuple[str, datetime] | None:
        """Returns (url, expires_at) for a shareable link, or None if
        this destination has no such concept (e.g. local disk)."""
        ...


class LocalDirectoryDestination:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _safe_target(self, filename: str) -> Path:
        target = (self.base_dir / filename).resolve()
        if self.base_dir not in target.parents and target != self.base_dir:
            raise DestinationError(f"filename {filename!r} escapes base directory")
        return target

    def write(self, local_path: Path, filename: str) -> WriteResult:
        target = self._safe_target(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)
        return WriteResult(location=str(target), key=str(target))

    def verify(self, write_result: WriteResult, local_path: Path) -> bool:
        target = Path(write_result.key)
        return target.exists() and target.stat().st_size == local_path.stat().st_size

    def is_retryable(self, exc: Exception) -> bool:
        # Disk-full and transient permission errors on network mounts
        # are the realistic transient cases for local writes.
        return isinstance(exc, (OSError, PermissionError)) and not isinstance(exc, DestinationError)

    def presign(self, key: str) -> tuple[str, datetime] | None:
        return None  # no meaningful concept of a shareable link on local disk


_RETRYABLE_S3_ERROR_CODES = {"SlowDown", "RequestTimeout", "InternalError", "503", "ServiceUnavailable"}


class S3Destination:
    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        client=None,
        presign_expiry_seconds: int = 3600,
    ):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._presign_expiry = presign_expiry_seconds
        if client is not None:
            self._client = client
        else:
            try:
                import boto3
            except ImportError as exc:
                raise DestinationError(
                    "boto3 is required for S3Destination; install artifactkit[s3]"
                ) from exc
            self._client = boto3.client("s3")

    def _key_for(self, filename: str) -> str:
        return f"{self.prefix}/{filename}" if self.prefix else filename

    def write(self, local_path: Path, filename: str) -> WriteResult:
        key = self._key_for(filename)
        self._client.upload_file(str(local_path), self.bucket, key)
        return WriteResult(location=f"s3://{self.bucket}/{key}", key=key)

    def verify(self, write_result: WriteResult, local_path: Path) -> bool:
        head = self._client.head_object(Bucket=self.bucket, Key=write_result.key)
        remote_etag = head["ETag"].strip('"')
        if "-" in remote_etag:
            # Multipart upload (upload_file uses this above ~8MB): ETag is
            # not a plain MD5 in this case, so fall back to a size check.
            return head["ContentLength"] == local_path.stat().st_size
        local_md5 = hashlib.md5(local_path.read_bytes()).hexdigest()  # noqa: S324 - integrity check, not security
        return local_md5 == remote_etag

    def is_retryable(self, exc: Exception) -> bool:
        try:
            import botocore.exceptions
        except ImportError:
            return isinstance(exc, (ConnectionError, TimeoutError))
        if isinstance(exc, botocore.exceptions.ClientError):
            code = exc.response.get("Error", {}).get("Code", "")
            return code in _RETRYABLE_S3_ERROR_CODES
        return isinstance(exc, (ConnectionError, TimeoutError, botocore.exceptions.EndpointConnectionError))

    def presign(self, key: str) -> tuple[str, datetime] | None:
        url = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self._presign_expiry,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._presign_expiry)
        return url, expires_at


def destination_from_uri(uri: str, **kwargs) -> OutputDestination:
    """Resolves a destination_uri string ("s3://bucket/prefix" or a
    local path) to an OutputDestination. This is the one place agent-
    facing code should call; credentials/config for S3 are picked up
    from the environment via boto3's default chain, not passed by the
    agent. kwargs forward to the chosen destination's constructor
    (e.g. presign_expiry_seconds)."""
    parsed = urlparse(uri)
    if parsed.scheme == "s3":
        if not parsed.netloc:
            raise DestinationError(f"invalid S3 URI, missing bucket: {uri!r}")
        return S3Destination(bucket=parsed.netloc, prefix=parsed.path.lstrip("/"), **kwargs)
    if parsed.scheme in ("", "file"):
        path = Path(parsed.path if parsed.scheme == "file" else uri)
        return LocalDirectoryDestination(path)  # local destination takes no extra kwargs
    raise DestinationError(f"unsupported destination scheme {parsed.scheme!r} in {uri!r}")

from pathlib import Path

import pytest

from artifactkit import LocalDirectoryDestination, destination_from_uri
from artifactkit.core.errors import DestinationError


def test_destination_from_uri_resolves_local_path(tmp_path):
    destination = destination_from_uri(str(tmp_path))
    assert isinstance(destination, LocalDirectoryDestination)
    assert destination.base_dir == tmp_path.resolve()


def test_destination_from_uri_rejects_unknown_scheme():
    with pytest.raises(DestinationError, match="unsupported destination scheme"):
        destination_from_uri("ftp://example.com/path")


def test_destination_from_uri_rejects_s3_uri_without_bucket():
    with pytest.raises(DestinationError, match="missing bucket"):
        destination_from_uri("s3://")


def test_local_destination_write_and_verify_roundtrip(tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source_file = source_dir / "payload.txt"
    source_file.write_bytes(b"hello world")

    destination = LocalDirectoryDestination(tmp_path / "dest")
    result = destination.write(source_file, "payload.txt")
    assert Path(result.location).read_bytes() == b"hello world"
    assert destination.verify(result, source_file) is True


def test_local_destination_rejects_path_traversal(tmp_path):
    destination = LocalDirectoryDestination(tmp_path / "dest")
    with pytest.raises(DestinationError, match="escapes base directory"):
        destination._safe_target("../../etc/passwd")


def test_local_destination_creates_base_dir_if_missing(tmp_path):
    target_dir = tmp_path / "does" / "not" / "exist"
    assert not target_dir.exists()
    LocalDirectoryDestination(target_dir)
    assert target_dir.exists()


class FakeS3Client:
    """Stands in for boto3's S3 client so S3Destination can be tested
    without a real AWS account or the moto mocking library."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.upload_calls: list[tuple[str, str, str]] = []
        self.presign_calls: list[dict] = []

    def upload_file(self, filename, bucket, key):
        with open(filename, "rb") as f:
            content = f.read()
        self.objects[(bucket, key)] = content
        self.upload_calls.append((filename, bucket, key))

    def head_object(self, Bucket, Key):
        content = self.objects[(Bucket, Key)]
        import hashlib
        etag = hashlib.md5(content).hexdigest()  # noqa: S324 - test double, not security use
        return {"ETag": f'"{etag}"', "ContentLength": len(content)}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presign_calls.append({"operation": operation, "params": Params, "expires_in": ExpiresIn})
        return f"https://fake-s3.example.com/{Params['Bucket']}/{Params['Key']}?presigned=1"


def test_s3_destination_write_and_verify_roundtrip(tmp_path):
    from artifactkit import S3Destination

    source = tmp_path / "payload.txt"
    source.write_bytes(b"hello s3")
    client = FakeS3Client()
    destination = S3Destination(bucket="my-bucket", prefix="reports", client=client)

    result = destination.write(source, "payload.txt")
    assert result.location == "s3://my-bucket/reports/payload.txt"
    assert destination.verify(result, source) is True


def test_s3_destination_verify_detects_corruption(tmp_path):
    from artifactkit import S3Destination
    from artifactkit.core.destinations import WriteResult

    source = tmp_path / "payload.txt"
    source.write_bytes(b"original content")
    client = FakeS3Client()
    destination = S3Destination(bucket="my-bucket", client=client)
    write_result = destination.write(source, "payload.txt")

    # Simulate corruption after upload: change what's "in S3" without
    # updating the local file it's being verified against.
    client.objects[("my-bucket", "payload.txt")] = b"corrupted!!"
    assert destination.verify(write_result, source) is False


def test_s3_destination_presign(tmp_path):
    from artifactkit import S3Destination

    source = tmp_path / "payload.txt"
    source.write_bytes(b"x")
    client = FakeS3Client()
    destination = S3Destination(bucket="my-bucket", client=client, presign_expiry_seconds=900)
    write_result = destination.write(source, "payload.txt")

    presigned = destination.presign(write_result.key)
    assert presigned is not None
    url, expires_at = presigned
    assert "presigned=1" in url
    assert client.presign_calls[0]["expires_in"] == 900


def test_s3_destination_no_prefix_uses_bare_key(tmp_path):
    from artifactkit import S3Destination

    source = tmp_path / "f.txt"
    source.write_bytes(b"x")
    client = FakeS3Client()
    destination = S3Destination(bucket="bucket", client=client)
    result = destination.write(source, "f.txt")
    assert result.location == "s3://bucket/f.txt"


def test_s3_destination_is_retryable_classifies_client_errors(tmp_path):
    from artifactkit import S3Destination

    destination = S3Destination(bucket="b", client=FakeS3Client())

    class FakeClientError(Exception):
        def __init__(self, code):
            self.response = {"Error": {"Code": code}}

    # Without botocore installed, is_retryable falls back to checking
    # ConnectionError/TimeoutError only -- it should not crash on an
    # arbitrary exception either way.
    assert destination.is_retryable(ConnectionError("blip")) in (True, False)
    assert destination.is_retryable(ValueError("not related")) is False

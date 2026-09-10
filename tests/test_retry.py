from pathlib import Path

import pytest

from artifactkit import ArtifactFormat, ArtifactService, DocumentSpec, Paragraph
from artifactkit.core.destinations import WriteResult
from artifactkit.core.errors import ArtifactWriteError


class FlakyDestination:
    """Fails write() a fixed number of times with a retryable error, then succeeds."""

    def __init__(self, tmp_path, fail_times: int, retryable: bool = True):
        self.tmp_path = tmp_path
        self.fail_times = fail_times
        self.retryable = retryable
        self.attempts = 0

    def write(self, local_path, filename):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise ConnectionError("simulated transient failure")
        target = self.tmp_path / filename
        target.write_bytes(local_path.read_bytes())
        return WriteResult(location=str(target), key=str(target))

    def verify(self, write_result, local_path):
        return Path(write_result.key).read_bytes() == local_path.read_bytes()

    def is_retryable(self, exc):
        return self.retryable and isinstance(exc, ConnectionError)

    def presign(self, key):
        return None


class AlwaysFailsVerifyDestination:
    """write() succeeds but verify() never confirms the content landed."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.write_calls = 0

    def write(self, local_path, filename):
        self.write_calls += 1
        target = self.tmp_path / filename
        target.write_bytes(local_path.read_bytes())
        return WriteResult(location=str(target), key=str(target))

    def verify(self, write_result, local_path):
        return False

    def is_retryable(self, exc):
        return isinstance(exc, ConnectionError)  # ArtifactWriteError is not this type

    def presign(self, key):
        return None


@pytest.fixture
def spec():
    return DocumentSpec(title="Retry test", blocks=(Paragraph.of("hello"),))


def test_retries_transient_failure_and_succeeds(spec, tmp_path):
    service = ArtifactService()
    destination = FlakyDestination(tmp_path, fail_times=2)
    result = service.create(spec, ArtifactFormat.DOCX, destination, filename="r.docx")
    assert result.write_attempts == 3
    assert Path(result.location).exists()


def test_gives_up_after_max_attempts(spec, tmp_path):
    service = ArtifactService()
    destination = FlakyDestination(tmp_path, fail_times=999)
    with pytest.raises(ConnectionError):
        service.create(spec, ArtifactFormat.DOCX, destination, filename="r.docx")
    assert destination.attempts == service._default_retry_policy.max_attempts


def test_non_retryable_exception_fails_immediately(spec, tmp_path):
    service = ArtifactService()
    destination = FlakyDestination(tmp_path, fail_times=999, retryable=False)
    with pytest.raises(ConnectionError):
        service.create(spec, ArtifactFormat.DOCX, destination, filename="r.docx")
    assert destination.attempts == 1


def test_verify_failure_is_not_retried_by_default(spec, tmp_path):
    """A write that 'succeeds' but fails verification is a correctness
    problem, not a transient one -- it should surface immediately, not
    retry and mask potential corruption."""
    service = ArtifactService()
    destination = AlwaysFailsVerifyDestination(tmp_path)
    with pytest.raises(ArtifactWriteError):
        service.create(spec, ArtifactFormat.DOCX, destination, filename="r.docx")
    assert destination.write_calls == 1

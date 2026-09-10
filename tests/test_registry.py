import pytest

from artifactkit.core.errors import UnsupportedFormatError
from artifactkit.core.models import ArtifactFormat
from artifactkit.core.registry import BackendRegistry, default_registry


def test_default_registry_has_all_four_backends():
    registry = default_registry()
    supported = set(registry.supported_formats())
    assert supported == {
        ArtifactFormat.DOCX,
        ArtifactFormat.PPTX,
        ArtifactFormat.XLSX,
        ArtifactFormat.PDF,
    }


def test_get_unregistered_format_raises_with_available_list():
    registry = BackendRegistry()
    registry.register(ArtifactFormat.DOCX, object())
    with pytest.raises(UnsupportedFormatError, match="docx"):
        registry.get(ArtifactFormat.PDF)


def test_get_on_empty_registry_reports_none_available():
    registry = BackendRegistry()
    with pytest.raises(UnsupportedFormatError, match="none"):
        registry.get(ArtifactFormat.DOCX)


def test_register_and_get_roundtrip():
    registry = BackendRegistry()
    sentinel = object()
    registry.register(ArtifactFormat.XLSX, sentinel)
    assert registry.get(ArtifactFormat.XLSX) is sentinel


def test_register_overwrites_existing_backend():
    registry = BackendRegistry()
    first, second = object(), object()
    registry.register(ArtifactFormat.DOCX, first)
    registry.register(ArtifactFormat.DOCX, second)
    assert registry.get(ArtifactFormat.DOCX) is second

"""Format -> backend lookup. New formats register here without touching
the service or any existing backend."""

from __future__ import annotations

from artifactkit.core.backend import ArtifactBackend
from artifactkit.core.errors import UnsupportedFormatError
from artifactkit.core.models import ArtifactFormat


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[ArtifactFormat, ArtifactBackend] = {}

    def register(self, fmt: ArtifactFormat, backend: ArtifactBackend) -> None:
        self._backends[fmt] = backend

    def get(self, fmt: ArtifactFormat) -> ArtifactBackend:
        try:
            return self._backends[fmt]
        except KeyError:
            available = ", ".join(f.value for f in self._backends) or "none"
            raise UnsupportedFormatError(
                f"no backend registered for {fmt.value!r}; available: {available}"
            ) from None

    def supported_formats(self) -> tuple[ArtifactFormat, ...]:
        return tuple(self._backends)


def default_registry() -> BackendRegistry:
    """Registry with all four v1 backends wired in. Import is deferred
    into this function so importing artifactkit.core doesn't force
    python-docx/python-pptx/openpyxl/reportlab to be installed for a
    consumer that only needs one format."""
    from artifactkit.backends.docx_backend import DocxBackend
    from artifactkit.backends.pdf_backend import PdfBackend
    from artifactkit.backends.pptx_backend import PptxBackend
    from artifactkit.backends.xlsx_backend import XlsxBackend

    registry = BackendRegistry()
    registry.register(ArtifactFormat.DOCX, DocxBackend())
    registry.register(ArtifactFormat.PPTX, PptxBackend())
    registry.register(ArtifactFormat.XLSX, XlsxBackend())
    registry.register(ArtifactFormat.PDF, PdfBackend())
    return registry

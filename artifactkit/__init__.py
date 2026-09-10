"""artifactkit: a shared artifact-creation harness for agentic workflows.

Public API surface — everything an agent or a plain-Python caller
needs is importable from the top level:

    from artifactkit import (
        ArtifactService, ArtifactFormat, ArtifactResult,
        DocumentSpec, PresentationSpec, WorkbookSpec,
        Heading, Paragraph, ListBlock, Table, Image, PageBreak, TextRun, TextStyle,
        destination_from_uri,
        ArtifactError, ArtifactValidationError, ArtifactWriteError,
    )
"""

from artifactkit.core.destinations import (
    LocalDirectoryDestination,
    OutputDestination,
    S3Destination,
    destination_from_uri,
)
from artifactkit.core.errors import (
    ArtifactError,
    ArtifactValidationError,
    ArtifactWriteError,
    DestinationError,
    UnsupportedFormatError,
)
from artifactkit.core.models import (
    ArtifactFormat,
    ArtifactSpec,
    DocumentSpec,
    FileBundleSpec,
    Heading,
    Image,
    ListBlock,
    PageBreak,
    Paragraph,
    PresentationSpec,
    Theme,
    RawFile,
    Sheet,
    Slide,
    Table,
    TextRun,
    TextStyle,
    WorkbookSpec,
)
from artifactkit.core.observability import LoggingMetricsSink, MetricsSink, NoOpMetricsSink
from artifactkit.core.retry import RetryPolicy
from artifactkit.core.service import ArtifactResult, ArtifactService, FileDeliveryResult

__version__ = "0.1.0"

__all__ = [
    "ArtifactService",
    "ArtifactResult",
    "ArtifactFormat",
    "ArtifactSpec",
    "RetryPolicy",
    "DocumentSpec",
    "Heading",
    "Paragraph",
    "ListBlock",
    "Table",
    "Image",
    "PageBreak",
    "TextRun",
    "TextStyle",
    "PresentationSpec",
    "Theme",
    "Slide",
    "WorkbookSpec",
    "Sheet",
    "RawFile",
    "FileBundleSpec",
    "FileDeliveryResult",
    "MetricsSink",
    "NoOpMetricsSink",
    "LoggingMetricsSink",
    "OutputDestination",
    "LocalDirectoryDestination",
    "S3Destination",
    "destination_from_uri",
    "ArtifactError",
    "ArtifactValidationError",
    "ArtifactWriteError",
    "UnsupportedFormatError",
    "DestinationError",
]

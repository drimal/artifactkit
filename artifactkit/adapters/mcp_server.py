"""FastMCP server exposing artifactkit as MCP tools.

Deliberately duplicates the tool signatures in strands_tools.py rather
than sharing decorated functions: @tool (Strands) and @mcp.tool
(FastMCP) wrap differently, and trying to share one decorated function
across both frameworks is exactly the kind of cross-framework coupling
this harness exists to avoid at the call site. The logic underneath -
parse spec, resolve destination, call ArtifactService - is shared via
core/, which is the actual point of de-duplication.

Run standalone with: python -m artifactkit.adapters.mcp_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from artifactkit.core.destinations import destination_from_uri
from artifactkit.core.errors import ArtifactError
from artifactkit.core.models import ArtifactFormat
from artifactkit.core.parsing import (
    parse_document_spec,
    parse_file_bundle_spec,
    parse_presentation_spec,
    parse_workbook_spec,
)
from artifactkit.core.service import ArtifactResult, ArtifactService, FileDeliveryResult

mcp = FastMCP("artifactkit")
_service = ArtifactService()


def _result_to_dict(result: ArtifactResult) -> dict:
    return {
        "location": result.location,
        "format": result.format.value,
        "size_bytes": result.size_bytes,
        "write_attempts": result.write_attempts,
        "shareable_url": result.presigned_url,
        "shareable_url_expires_at": (
            result.presigned_expires_at.isoformat() if result.presigned_expires_at else None
        ),
    }


def _delivery_result_to_dict(result: FileDeliveryResult) -> dict:
    return {
        "locations": list(result.locations),
        "bundled": result.bundled,
        "file_count": result.file_count,
        "total_size_bytes": result.total_size_bytes,
        "write_attempts": result.write_attempts,
        "shareable_url": result.presigned_url,
        "shareable_url_expires_at": (
            result.presigned_expires_at.isoformat() if result.presigned_expires_at else None
        ),
    }


@mcp.tool()
def create_docx(
    spec: dict, destination_uri: str, filename: str | None = None, include_shareable_link: bool = False
) -> dict:
    """Create a Word document from a structured content spec. See
    parse_document_spec for the expected spec shape."""
    try:
        result = _service.create(
            parse_document_spec(spec),
            ArtifactFormat.DOCX,
            destination_from_uri(destination_uri),
            filename=filename,
            include_presigned_url=include_shareable_link,
        )
        return _result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@mcp.tool()
def create_pptx(
    spec: dict, destination_uri: str, filename: str | None = None, include_shareable_link: bool = False
) -> dict:
    """Create a PowerPoint deck from a structured content spec. See
    parse_presentation_spec for the expected spec shape."""
    try:
        result = _service.create(
            parse_presentation_spec(spec),
            ArtifactFormat.PPTX,
            destination_from_uri(destination_uri),
            filename=filename,
            include_presigned_url=include_shareable_link,
        )
        return _result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@mcp.tool()
def create_xlsx(
    spec: dict, destination_uri: str, filename: str | None = None, include_shareable_link: bool = False
) -> dict:
    """Create an Excel workbook from a structured content spec. See
    parse_workbook_spec for the expected spec shape."""
    try:
        result = _service.create(
            parse_workbook_spec(spec),
            ArtifactFormat.XLSX,
            destination_from_uri(destination_uri),
            filename=filename,
            include_presigned_url=include_shareable_link,
        )
        return _result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@mcp.tool()
def create_pdf(
    spec: dict, destination_uri: str, filename: str | None = None, include_shareable_link: bool = False
) -> dict:
    """Create a PDF from a structured content spec. Uses the same spec
    shape as create_docx."""
    try:
        result = _service.create(
            parse_document_spec(spec),
            ArtifactFormat.PDF,
            destination_from_uri(destination_uri),
            filename=filename,
            include_presigned_url=include_shareable_link,
        )
        return _result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@mcp.tool()
def deliver_files(
    files: list[dict],
    destination_uri: str,
    bundle_filename: str | None = None,
    zip_threshold: int = 5,
    include_shareable_link: bool = False,
) -> dict:
    """Deliver an arbitrary set of files (code, images, data, anything
    with content already in hand) to destination_uri. No rendering
    happens; each file's content is written as-is.

    If more than zip_threshold files are given (default 5), they are
    bundled into a single .zip and delivered as one artifact instead
    of one write per file.

    files shape: [{"filename": "app.py", "content_base64": "..."}, ...]
    Each entry needs exactly one of content_base64 (base64-encoded
    bytes) or source_path (a path already on disk).
    """
    try:
        bundle_spec = parse_file_bundle_spec({"files": files, "bundle_filename": bundle_filename})
        result = _service.create_files(
            bundle_spec,
            destination_from_uri(destination_uri),
            zip_threshold=zip_threshold,
            include_presigned_url=include_shareable_link,
        )
        return _delivery_result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


if __name__ == "__main__":
    mcp.run()

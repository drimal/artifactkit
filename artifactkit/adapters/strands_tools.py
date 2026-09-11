"""Strands @tool wrappers around ArtifactService.

One tool per format rather than one combined tool with a format
parameter: a format-conditional schema is a poor tool-calling target,
separate tools give the model clean, unambiguous JSON schemas per call.
All four tools share one ArtifactService instance and destination
resolution logic, so there is exactly one place that knows how to talk
to S3 or local disk.
"""

from __future__ import annotations

from strands import tool

from artifactkit.backends.pptx_backend import inspect_template
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

_service = ArtifactService()


def _result_to_dict(result: ArtifactResult) -> dict:
    return {
        "location": result.location,
        "format": result.format.value,
        "size_bytes": result.size_bytes,
        "write_attempts": result.write_attempts,
        "operation_id": result.operation_id,
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
        "operation_id": result.operation_id,
        "shareable_url": result.presigned_url,
        "shareable_url_expires_at": (
            result.presigned_expires_at.isoformat() if result.presigned_expires_at else None
        ),
    }


@tool
def create_docx(
    spec: dict,
    destination_uri: str,
    filename: str | None = None,
    include_shareable_link: bool = False,
) -> dict:
    """Create a Word document from a structured content spec and write it
    to destination_uri (e.g. "s3://my-bucket/reports" or "/local/dir").

    spec shape: {"title": str, "blocks": [block, ...]}
    block types: heading, paragraph, list, table, image, page_break -
    each is a dict with a "type" key plus that block's fields.
    """
    try:
        document_spec = parse_document_spec(spec)
        destination = destination_from_uri(destination_uri)
        result = _service.create(
            document_spec,
            ArtifactFormat.DOCX,
            destination,
            filename=filename,
            include_presigned_url=include_shareable_link,
        )
        return _result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@tool
def create_pptx(
    spec: dict,
    destination_uri: str,
    filename: str | None = None,
    include_shareable_link: bool = False,
) -> dict:
    """Create a PowerPoint deck from a structured content spec and write
    it to destination_uri.

    spec shape: {"title": str, "slides": [slide, ...], "template_path": str|None}
    slide shape: {"layout": str, "placeholders": {"title": str, ...},
                  "images": [...], "speaker_notes": str|None}
    """
    try:
        presentation_spec = parse_presentation_spec(spec)
        destination = destination_from_uri(destination_uri)
        result = _service.create(
            presentation_spec,
            ArtifactFormat.PPTX,
            destination,
            filename=filename,
            include_presigned_url=include_shareable_link,
        )
        return _result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@tool
def create_xlsx(
    spec: dict,
    destination_uri: str,
    filename: str | None = None,
    include_shareable_link: bool = False,
) -> dict:
    """Create an Excel workbook from a structured content spec and write
    it to destination_uri.

    spec shape: {"sheets": [sheet, ...]}
    sheet shape: {"name": str, "header": [str, ...]|None,
                  "rows": [[cell, ...], ...], "formulas": {"D2": "=B2*C2"}}
    """
    try:
        workbook_spec = parse_workbook_spec(spec)
        destination = destination_from_uri(destination_uri)
        result = _service.create(
            workbook_spec,
            ArtifactFormat.XLSX,
            destination,
            filename=filename,
            include_presigned_url=include_shareable_link,
        )
        return _result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@tool
def create_pdf(
    spec: dict,
    destination_uri: str,
    filename: str | None = None,
    include_shareable_link: bool = False,
) -> dict:
    """Create a PDF from a structured content spec and write it to
    destination_uri. Uses the same spec shape as create_docx."""
    try:
        document_spec = parse_document_spec(spec)
        destination = destination_from_uri(destination_uri)
        result = _service.create(
            document_spec,
            ArtifactFormat.PDF,
            destination,
            filename=filename,
            include_presigned_url=include_shareable_link,
        )
        return _result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@tool
def deliver_files(
    files: list[dict],
    destination_uri: str,
    bundle_filename: str | None = None,
    zip_threshold: int = 5,
    include_shareable_link: bool = False,
) -> dict:
    """Deliver an arbitrary set of files, code, images, data, anything
    with content already in hand, to destination_uri. No rendering
    happens; each file's content is written as-is.

    If more than zip_threshold files are given (default 5), they are
    bundled into a single .zip and delivered as one artifact instead
    of one write per file.

    files shape: [{"filename": "app.py", "content_base64": "..."}, ...]
    Each entry needs exactly one of content_base64 (base64-encoded
    bytes) or source_path (a path already on disk, e.g. output from
    code the agent ran -- avoids hauling large content through the
    model's context just to hand it back here).
    """
    try:
        bundle_spec = parse_file_bundle_spec(
            {"files": files, "bundle_filename": bundle_filename}
        )
        destination = destination_from_uri(destination_uri)
        result = _service.create_files(
            bundle_spec,
            destination,
            zip_threshold=zip_threshold,
            include_presigned_url=include_shareable_link,
        )
        return _delivery_result_to_dict(result)
    except ArtifactError as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


@tool
def inspect_pptx_template(template_path: str) -> dict:
    """Reads a .pptx template file and reports its slide layouts and
    each layout's placeholders (index, shape name, type), without
    rendering anything. Call this before building a create_pptx spec
    that targets a custom template, so the layout name and
    placeholder keys ("idx:N" or an exact placeholder name) can be
    chosen correctly instead of guessed.
    """
    try:
        info = inspect_template(template_path)
        return {
            "layouts": [
                {
                    "index": layout.index,
                    "name": layout.name,
                    "placeholders": [
                        {"idx": ph.idx, "name": ph.name, "type": ph.type}
                        for ph in layout.placeholders
                    ],
                }
                for layout in info.layouts
            ]
        }
    except Exception as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}

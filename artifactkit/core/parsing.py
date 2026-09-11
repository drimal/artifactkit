"""Converts plain dicts (as received from an LLM tool call) into the
typed spec dataclasses. Kept separate from models.py so the dataclasses
themselves stay free of parsing concerns; this module is the one place
that knows the JSON shape agents are expected to emit."""

from __future__ import annotations

import base64
import binascii

from artifactkit.core.errors import ArtifactError
from artifactkit.core.models import (
    CellValueRule,
    ColorScaleRule,
    DataBarRule,
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

_BLOCK_PARSERS = {}


def _block_parser(type_name: str):
    def decorator(fn):
        _BLOCK_PARSERS[type_name] = fn
        return fn

    return decorator


@_block_parser("heading")
def _parse_heading(d: dict) -> Heading:
    return Heading(text=d["text"], level=d.get("level", 1))


@_block_parser("paragraph")
def _parse_paragraph(d: dict) -> Paragraph:
    if "runs" in d:
        runs = tuple(TextRun(r["text"], TextStyle(r.get("style", "normal"))) for r in d["runs"])
        return Paragraph(runs=runs)
    return Paragraph.of(d["text"], TextStyle(d.get("style", "normal")))


@_block_parser("list")
def _parse_list(d: dict) -> ListBlock:
    return ListBlock(items=tuple(d["items"]), ordered=d.get("ordered", False))


@_block_parser("table")
def _parse_table(d: dict) -> Table:
    return Table(headers=tuple(d["headers"]), rows=tuple(tuple(row) for row in d["rows"]))


@_block_parser("image")
def _parse_image(d: dict) -> Image:
    return Image(
        source_path=d["source_path"],
        caption=d.get("caption"),
        width_inches=d.get("width_inches"),
    )


@_block_parser("page_break")
def _parse_page_break(d: dict) -> PageBreak:
    return PageBreak()


def parse_document_spec(d: dict) -> DocumentSpec:
    try:
        blocks = tuple(_parse_block(b) for b in d["blocks"])
        return DocumentSpec(
            title=d["title"],
            blocks=blocks,
            base_filename=d.get("base_filename"),
            theme=Theme(d["theme"]) if "theme" in d else None,
        )
    except KeyError as exc:
        raise ArtifactError(f"document spec missing required field: {exc}") from exc
    except ValueError as exc:
        raise ArtifactError(f"invalid theme in document spec: {exc}") from exc


def _parse_block(d: dict):
    block_type = d.get("type")
    parser = _BLOCK_PARSERS.get(block_type)
    if parser is None:
        raise ArtifactError(
            f"unknown block type {block_type!r}; expected one of {sorted(_BLOCK_PARSERS)}"
        )
    return parser(d)


def parse_presentation_spec(d: dict) -> PresentationSpec:
    try:
        slides = tuple(
            Slide(
                layout=s["layout"],
                placeholders=s["placeholders"],
                images=tuple(_parse_image(i) for i in s.get("images", [])),
                speaker_notes=s.get("speaker_notes"),
            )
            for s in d["slides"]
        )
        return PresentationSpec(
            title=d["title"],
            slides=slides,
            template_path=d.get("template_path"),
            base_filename=d.get("base_filename"),
            theme=Theme(d["theme"]) if "theme" in d else None,
        )
    except KeyError as exc:
        raise ArtifactError(f"presentation spec missing required field: {exc}") from exc
    except ValueError as exc:
        raise ArtifactError(f"invalid theme in presentation spec: {exc}") from exc


def _parse_conditional_format_rule(d: dict):
    rule_type = d.get("type")
    if rule_type == "color_scale":
        return ColorScaleRule(cell_range=d["cell_range"], colors=tuple(d["colors"]))
    if rule_type == "cell_value":
        return CellValueRule(
            cell_range=d["cell_range"],
            operator=d["operator"],
            values=tuple(d["values"]),
            fill_hex=d["fill_hex"],
            font_hex=d.get("font_hex"),
            bold=d.get("bold", False),
        )
    if rule_type == "data_bar":
        return DataBarRule(cell_range=d["cell_range"], color_hex=d["color_hex"])
    raise ArtifactError(
        f"unknown conditional format rule type {rule_type!r}; "
        "expected one of 'color_scale', 'cell_value', 'data_bar'"
    )


def parse_workbook_spec(d: dict) -> WorkbookSpec:
    try:
        sheets = tuple(
            Sheet(
                name=s["name"],
                header=tuple(s["header"]) if "header" in s else None,
                rows=tuple(tuple(row) for row in s.get("rows", [])),
                formulas=s.get("formulas", {}),
                conditional_formats=tuple(
                    _parse_conditional_format_rule(r) for r in s.get("conditional_formats", [])
                ),
            )
            for s in d["sheets"]
        )
        return WorkbookSpec(
            sheets=sheets,
            base_filename=d.get("base_filename"),
            theme=Theme(d["theme"]) if "theme" in d else None,
        )
    except KeyError as exc:
        raise ArtifactError(f"workbook spec missing required field: {exc}") from exc
    except ValueError as exc:
        raise ArtifactError(f"invalid theme or conditional format rule in workbook spec: {exc}") from exc


def parse_file_bundle_spec(d: dict) -> FileBundleSpec:
    """Expects {"files": [...], "bundle_filename": str | None}. Each
    file entry needs "filename" plus exactly one of:
    - "content_base64": inline content, base64-encoded (raw bytes can't
      ride in a JSON payload directly)
    - "source_path": a path already on disk (e.g. output from code the
      agent ran) -- avoids hauling large content through the model's
      context just to hand it back to this tool."""
    try:
        files = tuple(_parse_raw_file(f) for f in d["files"])
        return FileBundleSpec(files=files, bundle_filename=d.get("bundle_filename"))
    except KeyError as exc:
        raise ArtifactError(f"file bundle spec missing required field: {exc}") from exc
    except binascii.Error as exc:
        raise ArtifactError(f"invalid base64 content in file bundle spec: {exc}") from exc


def _parse_raw_file(f: dict) -> RawFile:
    filename = f["filename"]
    has_content = "content_base64" in f
    has_path = "source_path" in f
    if has_content == has_path:
        raise ArtifactError(
            f"file entry {filename!r} needs exactly one of content_base64 or source_path"
        )
    if has_path:
        return RawFile(filename=filename, source_path=f["source_path"])
    return RawFile(filename=filename, content=base64.b64decode(f["content_base64"]))

"""Declarative content specs for artifactkit.

These are the intermediate representations agents build and hand to
``ArtifactService.create()``. Backends render them; agents never touch
python-docx / python-pptx / openpyxl directly.

Two design rules kept deliberately:
1. Specs are plain dataclasses, not builders. Agents (or an LLM emitting
   JSON that gets parsed into these) construct them directly.
2. Blocks/cells use a common ABC per spec type rather than one universal
   block model. Documents, slides, and spreadsheets are structurally
   different; forcing them through a single shape adds indirection
   without adding capability.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ArtifactFormat(str, Enum):
    """Output format, doubles as the backend registry key."""

    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    PDF = "pdf"
    ZIP = "zip"  # used only as the result tag when create_files() bundles


def _validate_base_filename(name: str | None) -> None:
    """base_filename is a bare stem: no extension (the backend decides
    that from ArtifactFormat) and no path separators (it's a name, not
    a location — destinations own path safety)."""
    if name is None:
        return
    if not name:
        raise ValueError("base_filename must not be empty when provided")
    if "/" in name or "\\" in name:
        raise ValueError(f"base_filename must not contain path separators: {name!r}")
    if "." in name:
        raise ValueError(
            f"base_filename must be a bare name with no extension: {name!r} "
            "(the backend appends the extension for its format)"
        )


class TextStyle(str, Enum):
    """Inline emphasis for a TextRun. Kept to the common denominator
    across docx/pptx/pdf renderers; anything fancier is out of scope
    for v1 (see YAGNI note in the harness design)."""

    NORMAL = "normal"
    BOLD = "bold"
    ITALIC = "italic"
    BOLD_ITALIC = "bold_italic"


class Theme(str, Enum):
    """Named visual preset a backend applies automatically when set on
    a spec's `theme` field — DocxBackend, PptxBackend, and XlsxBackend
    each interpret these three names in a way that fits their format
    (accent colors and borders in a document, gradients and shapes in
    a deck, header fill and table style in a workbook). None (the
    default) means no styling — each backend's bare output as before.
    The concrete colors/fonts/styles per preset live in each backend,
    not here, since they're a rendering detail, not spec data."""

    VIBRANT = "vibrant"
    CORPORATE = "corporate"
    MINIMAL = "minimal"


# --------------------------------------------------------------------------
# Document spec (docx / pdf / markdown-flavored content)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TextRun:
    """A styled span of text within a Paragraph."""

    text: str
    style: TextStyle = TextStyle.NORMAL

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("TextRun.text must not be empty")


class Block(ABC):
    """Marker base for anything that can appear in a DocumentSpec body."""


@dataclass(frozen=True)
class Heading(Block):
    text: str
    level: int = 1  # 1 = title-level, up to 4 supported by all v1 backends

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("Heading.text must not be empty")
        if not 1 <= self.level <= 4:
            raise ValueError(f"Heading.level must be 1-4, got {self.level}")


@dataclass(frozen=True)
class Paragraph(Block):
    """Plain paragraphs pass a single TextRun; use multiple runs only
    when mixing styles (e.g. bold term followed by normal definition)."""

    runs: tuple[TextRun, ...]

    def __post_init__(self) -> None:
        if not self.runs:
            raise ValueError("Paragraph.runs must not be empty")

    @classmethod
    def of(cls, text: str, style: TextStyle = TextStyle.NORMAL) -> "Paragraph":
        """Convenience constructor for the common single-run case."""
        return cls(runs=(TextRun(text, style),))


@dataclass(frozen=True)
class ListBlock(Block):
    items: tuple[str, ...]
    ordered: bool = False

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("ListBlock.items must not be empty")


@dataclass(frozen=True)
class Table(Block):
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        if not self.headers:
            raise ValueError("Table.headers must not be empty")
        for i, row in enumerate(self.rows):
            if len(row) != len(self.headers):
                raise ValueError(
                    f"Table row {i} has {len(row)} cells, expected {len(self.headers)}"
                )


@dataclass(frozen=True)
class Image(Block):
    source_path: str  # local path, resolved before render; not a URI
    caption: str | None = None
    width_inches: float | None = None  # None lets the backend pick a default


@dataclass(frozen=True)
class PageBreak(Block):
    pass


@dataclass(frozen=True)
class DocumentSpec:
    """A flowing document: title plus an ordered sequence of blocks.
    Renders to docx or pdf via DocxBackend / PdfBackend.

    Attributes:
        title: Document title, rendered as the top-level heading.
        blocks: Ordered content — Heading, Paragraph, ListBlock, Table,
            Image, or PageBreak.
        base_filename: Optional bare filename (no extension) that
            ArtifactService.create() uses when no explicit filename is
            given, adapted to whichever format is requested.
        theme: Optional named preset DocxBackend applies automatically
            (heading colors, an accent rule under the title, styled
            table headers). PdfBackend currently ignores this — see
            PdfBackend for why.
    """

    title: str
    blocks: tuple[Block, ...]
    base_filename: str | None = None
    theme: Theme | None = None

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("DocumentSpec.title must not be empty")
        if not self.blocks:
            raise ValueError("DocumentSpec.blocks must not be empty")
        _validate_base_filename(self.base_filename)


# --------------------------------------------------------------------------
# Presentation spec (pptx)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Slide:
    """One slide. layout is matched against the template's slide
    layouts by name (case-insensitive), with a small set of common
    names ("title", "title_and_body", etc.) falling back to python-pptx's
    default template layouts when no template_path is given."""

    layout: str
    placeholders: dict[str, str]
    images: tuple[Image, ...] = field(default_factory=tuple)
    speaker_notes: str | None = None

    def __post_init__(self) -> None:
        if not self.layout:
            raise ValueError("Slide.layout must not be empty")
        if not self.placeholders:
            raise ValueError("Slide.placeholders must not be empty")


@dataclass(frozen=True)
class PresentationSpec:
    """A slide deck: title plus an ordered sequence of Slides. Renders
    to pptx via PptxBackend.

    Attributes:
        title: Deck title (not necessarily shown on a slide).
        slides: Ordered slides.
        template_path: Optional .pptx template to base layouts on. None
            uses python-pptx's built-in default template.
        base_filename: Optional bare filename (no extension), same role
            as DocumentSpec.base_filename.
        theme: Optional named preset (background, title/body styling,
            accent bar) PptxBackend applies automatically. None means
            no styling — the raw template as-is.
    """

    title: str
    slides: tuple[Slide, ...]
    template_path: str | None = None
    base_filename: str | None = None
    theme: Theme | None = None

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("PresentationSpec.title must not be empty")
        if not self.slides:
            raise ValueError("PresentationSpec.slides must not be empty")
        _validate_base_filename(self.base_filename)


# --------------------------------------------------------------------------
# Workbook spec (xlsx)
# --------------------------------------------------------------------------


_CELL_VALUE_OPERATORS = frozenset({
    "greaterThan", "lessThan", "equal", "notEqual",
    "greaterThanOrEqual", "lessThanOrEqual", "between",
})
_TWO_VALUE_OPERATORS = frozenset({"between"})


@dataclass(frozen=True)
class ColorScaleRule:
    """A 2- or 3-color heatmap over a cell range, low to high — the
    classic red/yellow/green "which values stand out" visualization."""

    cell_range: str  # e.g. "B2:B10"
    colors: tuple[str, ...]  # 2 or 3 hex colors, low -> (mid) -> high

    def __post_init__(self) -> None:
        if not self.cell_range:
            raise ValueError("ColorScaleRule.cell_range must not be empty")
        if len(self.colors) not in (2, 3):
            raise ValueError(
                f"ColorScaleRule.colors must have 2 or 3 colors, got {len(self.colors)}"
            )


@dataclass(frozen=True)
class CellValueRule:
    """Highlights cells whose value satisfies a comparison (e.g. > 100)
    with a fill color and optional font styling."""

    cell_range: str
    operator: str  # one of _CELL_VALUE_OPERATORS
    values: tuple[str, ...]  # 1 value normally, 2 for "between"
    fill_hex: str
    font_hex: str | None = None
    bold: bool = False

    def __post_init__(self) -> None:
        if not self.cell_range:
            raise ValueError("CellValueRule.cell_range must not be empty")
        if self.operator not in _CELL_VALUE_OPERATORS:
            raise ValueError(
                f"CellValueRule.operator must be one of {sorted(_CELL_VALUE_OPERATORS)}, "
                f"got {self.operator!r}"
            )
        expected = 2 if self.operator in _TWO_VALUE_OPERATORS else 1
        if len(self.values) != expected:
            raise ValueError(
                f"CellValueRule with operator {self.operator!r} needs {expected} "
                f"value(s), got {len(self.values)}"
            )


@dataclass(frozen=True)
class DataBarRule:
    """An in-cell bar proportional to the cell's value relative to the
    range's min/max — quick visual scanning without reading numbers."""

    cell_range: str
    color_hex: str

    def __post_init__(self) -> None:
        if not self.cell_range:
            raise ValueError("DataBarRule.cell_range must not be empty")


ConditionalFormatRule = ColorScaleRule | CellValueRule | DataBarRule


@dataclass(frozen=True)
class Sheet:
    """One worksheet. formulas maps a cell reference to a formula
    string (e.g. {"D2": "=B2*C2"}), written verbatim by openpyxl.

    Note: openpyxl writes formulas with no cached value, so a formula
    cell reads back as None until Excel or LibreOffice recalculates
    the file. XlsxBackend does not attempt recalculation itself.
    """

    name: str
    header: tuple[str, ...] | None = None
    rows: tuple[tuple[str | int | float | None, ...], ...] = field(default_factory=tuple)
    formulas: dict[str, str] = field(default_factory=dict)
    conditional_formats: tuple[ConditionalFormatRule, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Sheet.name must not be empty")
        expected_width = len(self.header) if self.header else None
        for i, row in enumerate(self.rows):
            if expected_width is not None and len(row) != expected_width:
                raise ValueError(
                    f"Sheet '{self.name}' row {i} has {len(row)} cells, "
                    f"expected {expected_width} to match header"
                )


@dataclass(frozen=True)
class WorkbookSpec:
    """A spreadsheet: one or more Sheets. Renders to xlsx via
    XlsxBackend.

    Attributes:
        sheets: Worksheets, in tab order. Sheet names must be unique.
        base_filename: Optional bare filename (no extension), same role
            as DocumentSpec.base_filename.
        theme: Optional named preset XlsxBackend applies automatically
            (header row fill, a native Excel Table with banded rows,
            sheet tab color, frozen header row). Only applies to sheets
            that have a header — see XlsxBackend for why.
    """

    sheets: tuple[Sheet, ...]
    base_filename: str | None = None
    theme: Theme | None = None

    def __post_init__(self) -> None:
        if not self.sheets:
            raise ValueError("WorkbookSpec.sheets must not be empty")
        names = [s.name for s in self.sheets]
        if len(names) != len(set(names)):
            raise ValueError(f"WorkbookSpec has duplicate sheet names: {names}")
        _validate_base_filename(self.base_filename)


# --------------------------------------------------------------------------
# Union type used by the service / registry
# --------------------------------------------------------------------------

ArtifactSpec = DocumentSpec | PresentationSpec | WorkbookSpec


# --------------------------------------------------------------------------
# Raw file delivery (any file type: code, images, data files, anything
# with content the caller already has fully formed). Deliberately kept
# separate from ArtifactSpec/ArtifactFormat above: those describe
# content to be *rendered* by a backend into one fixed format. A raw
# file is already-rendered bytes with a caller-chosen name and
# extension, so it skips the render/validate pipeline and goes
# straight to write + verify. See ArtifactService.create_files().
# --------------------------------------------------------------------------


def _validate_raw_filename(name: str) -> None:
    """Unlike base_filename, a raw filename keeps its own extension —
    the whole point is the caller controls it (app.py, diagram.png,
    Dockerfile). Only path safety is enforced here."""
    if not name:
        raise ValueError("filename must not be empty")
    if "/" in name or "\\" in name:
        raise ValueError(f"filename must not contain path separators: {name!r}")
    if name in (".", ".."):
        raise ValueError(f"filename must not be a relative path segment: {name!r}")


@dataclass(frozen=True)
class RawFile:
    """One already-formed file for ArtifactService.create_files(). No
    rendering happens on this content; it's written as-is.

    Attributes:
        filename: Full name including extension, e.g. "app.py",
            "diagram.png". Keeps its own extension (unlike
            base_filename above) since the caller controls it directly.
        content: In-memory bytes. Mutually exclusive with source_path —
            exactly one of the two must be set.
        source_path: Path to an existing file on disk, read lazily at
            write time rather than loaded upfront. Useful right after
            running code that wrote its output somewhere, without a
            manual read-into-bytes step.
    """

    filename: str
    content: bytes | None = None
    source_path: str | None = None

    def __post_init__(self) -> None:
        _validate_raw_filename(self.filename)
        if (self.content is None) == (self.source_path is None):
            raise ValueError(
                f"RawFile({self.filename!r}) requires exactly one of content or "
                "source_path, not both and not neither"
            )
        if self.content is not None and not isinstance(self.content, bytes):
            raise TypeError(f"RawFile.content must be bytes, got {type(self.content).__name__}")


@dataclass(frozen=True)
class FileBundleSpec:
    """One or more RawFiles delivered together via
    ArtifactService.create_files(). Above a caller-chosen file-count
    threshold, the whole bundle is zipped into a single archive
    instead of writing each file separately — see create_files() for
    the threshold logic.

    Attributes:
        files: The files to deliver. Filenames must be unique within
            the bundle.
        bundle_filename: Optional bare name (no ".zip") used only if
            the file count triggers bundling; defaults to "artifacts".
    """

    files: tuple[RawFile, ...]
    bundle_filename: str | None = None

    def __post_init__(self) -> None:
        if not self.files:
            raise ValueError("FileBundleSpec.files must not be empty")
        names = [f.filename for f in self.files]
        if len(names) != len(set(names)):
            raise ValueError(f"FileBundleSpec has duplicate filenames: {names}")
        _validate_base_filename(self.bundle_filename)

    @classmethod
    def from_directory(cls, directory: str | Path, bundle_filename: str | None = None) -> "FileBundleSpec":
        """Builds a bundle from every file directly inside directory
        (non-recursive: RawFile.filename can't hold path separators, so
        nested structure has nowhere to go). Each RawFile references its
        source_path rather than loading content upfront — useful right
        after running code that wrote its output to a scratch directory."""
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise ValueError(f"not a directory: {directory!r}")
        files = tuple(
            RawFile(filename=p.name, source_path=str(p))
            for p in sorted(dir_path.iterdir())
            if p.is_file()
        )
        if not files:
            raise ValueError(f"no files found directly inside {directory!r}")
        return cls(files=files, bundle_filename=bundle_filename)


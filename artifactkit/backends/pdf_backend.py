"""Renders DocumentSpec to .pdf via reportlab's platypus layer.

Note: never emit Unicode subscript/superscript characters here -
reportlab's built-in fonts don't include those glyphs and they render
as solid black boxes. Use plain text; callers needing sub/superscript
should use reportlab's XML markup in a future TextRun extension, not
raw Unicode."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    ListFlowable,
    ListItem,
    PageBreak as RLPageBreak,
    Paragraph as RLParagraph,
    SimpleDocTemplate,
    Spacer,
    Table as RLTable,
    TableStyle,
)

from artifactkit.core.backend import ValidationResult
from artifactkit.core.models import (
    ArtifactSpec,
    Block,
    DocumentSpec,
    Heading,
    Image,
    ListBlock,
    PageBreak,
    Paragraph,
    Table,
)

_STYLES = getSampleStyleSheet()
_HEADING_STYLES = {1: "Heading1", 2: "Heading2", 3: "Heading3", 4: "Heading4"}


class PdfBackend:
    def render(self, spec: ArtifactSpec, output_path: Path) -> None:
        assert isinstance(spec, DocumentSpec)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(str(output_path), pagesize=letter)

        story = [RLParagraph(spec.title, _STYLES["Title"]), Spacer(1, 0.25 * inch)]
        for block in spec.blocks:
            story.extend(self._render_block(block))

        doc.build(story)

    def _render_block(self, block: Block) -> list:
        if isinstance(block, Heading):
            style_name = _HEADING_STYLES.get(block.level, "Heading4")
            return [RLParagraph(block.text, _STYLES[style_name])]
        if isinstance(block, Paragraph):
            text = "".join(self._run_to_markup(run) for run in block.runs)
            return [RLParagraph(text, _STYLES["Normal"]), Spacer(1, 0.1 * inch)]
        if isinstance(block, ListBlock):
            items = [ListItem(RLParagraph(item, _STYLES["Normal"])) for item in block.items]
            bullet_type = "1" if block.ordered else "bullet"
            return [ListFlowable(items, bulletType=bullet_type), Spacer(1, 0.1 * inch)]
        if isinstance(block, Table):
            data = [list(block.headers)] + [list(row) for row in block.rows]
            table = RLTable(data)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ]
                )
            )
            return [table, Spacer(1, 0.15 * inch)]
        if isinstance(block, Image):
            kwargs = {"width": block.width_inches * inch} if block.width_inches else {}
            flowables: list = [RLImage(block.source_path, **kwargs)]
            if block.caption:
                flowables.append(RLParagraph(block.caption, _STYLES["Italic"]))
            flowables.append(Spacer(1, 0.15 * inch))
            return flowables
        if isinstance(block, PageBreak):
            return [RLPageBreak()]
        raise TypeError(f"PdfBackend has no renderer for block type {type(block).__name__}")

    def _run_to_markup(self, run) -> str:
        from artifactkit.core.models import TextStyle

        if run.style == TextStyle.BOLD:
            return f"<b>{run.text}</b>"
        if run.style == TextStyle.ITALIC:
            return f"<i>{run.text}</i>"
        if run.style == TextStyle.BOLD_ITALIC:
            return f"<b><i>{run.text}</i></b>"
        return run.text

    def validate(self, output_path: Path, spec: ArtifactSpec) -> ValidationResult:
        assert isinstance(spec, DocumentSpec)
        errors: list[str] = []
        checks: list[str] = []
        try:
            reader = PdfReader(output_path)
            checks.append("file_opens")
        except Exception as exc:
            return ValidationResult(is_valid=False, errors=(f"unreadable: {exc}",))

        if len(reader.pages) == 0:
            errors.append("PDF has zero pages")
        else:
            checks.append("nonzero_pages")

        return ValidationResult(is_valid=not errors, checks_passed=tuple(checks), errors=tuple(errors))

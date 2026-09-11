"""Renders PresentationSpec to .pptx via python-pptx.

Layout resolution: Slide.layout is matched by name against the
template's slide_layouts (case-insensitive). If no template is given,
python-pptx's default template is used and layout falls back to index
lookup against its five standard layouts.

Placeholder targeting: Slide.placeholders keys are resolved in this
order, so old specs keep working exactly as before while new specs
can target a custom template precisely instead of guessing:
  1. "title" -- the layout's title placeholder (unchanged, most common case)
  2. "idx:N" -- the placeholder with that exact placeholder_format.idx,
     found via inspect_template()
  3. an exact placeholder shape name (e.g. "Content Placeholder 2"),
     also found via inspect_template()
  4. anything left over fills remaining, unfilled placeholders in
     declaration order -- the original positional-fill behavior,
     preserved for backward compatibility and for the common case of
     a single title+body slide where precision doesn't matter.

Template introspection: inspect_template(path) reads a .pptx file and
reports every layout's name, index, and placeholders (idx, name, type)
without rendering anything -- call it before building a spec that
targets a custom template, rather than guessing what's in it.

Theming: PresentationSpec.theme is applied only when template_path is
NOT set. A custom template already carries its own intentional design;
forcing a gradient/accent-bar theme on top of it would fight that
design rather than respect it. When both are set, theme is skipped
with a warning log, not silently -- so the "why didn't my theme show
up" question has an answer in the logs.

Title-type slides ("title" / "section_header" layouts) get a gradient
hero background with light title text and a decorative corner shape
when theme is active -- the bold, attention-grabbing treatment a human
designer would give an opening/section slide. Every other slide gets a
clean, readable solid background with a thin accent bar.

All of this is pure python-pptx: solid/gradient fills, font
color/size/weight, and preset auto-shapes. No external rendering
engine (no pptxgenjs, no Node runtime) -- this backend has to run
unattended inside deployed agent infrastructure that may not have
Node available, so the styling ceiling here is "everything python-pptx
can natively do," not "everything a JS rendering library can do."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

from artifactkit.core.backend import ValidationResult
from artifactkit.core.models import ArtifactSpec, PresentationSpec, Theme

logger = logging.getLogger("artifactkit")

_DEFAULT_LAYOUT_NAMES = {
    "title": 0,
    "title_and_body": 1,
    "section_header": 2,
    "two_content": 3,
    "title_only": 5,
    "blank": 6,
}

# Layout names treated as "hero" slides (gradient background, decorative
# shape) rather than "content" slides (light background, accent bar).
_HERO_LAYOUTS = {"title", "section_header"}


@dataclass(frozen=True)
class PlaceholderInfo:
    """One placeholder on a slide layout, as reported by inspect_template()."""

    idx: int
    name: str  # shape name, e.g. "Content Placeholder 2" -- usable as a Slide.placeholders key
    type: str  # placeholder_format.type name, e.g. "TITLE", "BODY", "PICTURE", "SUBTITLE"


@dataclass(frozen=True)
class LayoutInfo:
    """One slide layout, as reported by inspect_template()."""

    index: int  # position in presentation.slide_layouts -- usable as Slide.layout
    name: str  # layout name, e.g. "Title and Content" -- also usable as Slide.layout
    placeholders: tuple[PlaceholderInfo, ...]


@dataclass(frozen=True)
class TemplateInfo:
    """Result of inspect_template(): every layout in a .pptx template."""

    layouts: tuple[LayoutInfo, ...]


def inspect_template(template_path: str) -> TemplateInfo:
    """Reads a .pptx file and reports its slide layouts and each
    layout's placeholders, without rendering anything. Call this
    before building a PresentationSpec that targets a custom
    template, so slide.placeholders keys ("idx:N" or an exact
    placeholder name) can be chosen correctly instead of guessed."""
    presentation = Presentation(template_path)
    layouts = tuple(
        LayoutInfo(
            index=index,
            name=layout.name,
            placeholders=tuple(
                PlaceholderInfo(
                    idx=ph.placeholder_format.idx,
                    name=ph.name,
                    type=str(ph.placeholder_format.type) if ph.placeholder_format.type is not None else "UNKNOWN",
                )
                for ph in layout.placeholders
            ),
        )
        for index, layout in enumerate(presentation.slide_layouts)
    )
    return TemplateInfo(layouts=layouts)


@dataclass(frozen=True)
class _ThemeStyle:
    # Hero slides (title / section_header layouts): gradient background.
    gradient_start_hex: str
    gradient_end_hex: str
    gradient_angle: float
    hero_text_hex: str  # title text color on the gradient — must read on dark
    decorative_hex: str  # corner shape color on hero slides

    # Content slides (everything else): solid background, accent bar.
    content_background_hex: str
    content_title_hex: str
    body_hex: str
    accent_hex: str

    font_name: str
    title_size_pt: int = 40


# Concrete colors/fonts per preset live here, not on Theme
# itself — the enum is spec data (what the agent asked for), this
# mapping is a rendering detail specific to PptxBackend.
_THEME_STYLES: dict[Theme, _ThemeStyle] = {
    Theme.VIBRANT: _ThemeStyle(
        gradient_start_hex="E94560",
        gradient_end_hex="533483",
        gradient_angle=45.0,
        hero_text_hex="FFFFFF",
        decorative_hex="0F3460",
        content_background_hex="FFFFFF",
        content_title_hex="E94560",
        body_hex="16213E",
        accent_hex="F0A500",
        font_name="Calibri",
        title_size_pt=40,
    ),
    Theme.CORPORATE: _ThemeStyle(
        gradient_start_hex="1F3A5F",
        gradient_end_hex="4A90D9",
        gradient_angle=90.0,
        hero_text_hex="FFFFFF",
        decorative_hex="2C5F8A",
        content_background_hex="FFFFFF",
        content_title_hex="1F3A5F",
        body_hex="333333",
        accent_hex="4A90D9",
        font_name="Georgia",
        title_size_pt=36,
    ),
    Theme.MINIMAL: _ThemeStyle(
        gradient_start_hex="1A1A1A",
        gradient_end_hex="3D3D3D",
        gradient_angle=135.0,
        hero_text_hex="FFFFFF",
        decorative_hex="CCCCCC",
        content_background_hex="FFFFFF",
        content_title_hex="1A1A1A",
        body_hex="444444",
        accent_hex="999999",
        font_name="Helvetica",
        title_size_pt=34,
    ),
}


class PptxBackend:
    def render(self, spec: ArtifactSpec, output_path: Path) -> None:
        assert isinstance(spec, PresentationSpec)
        presentation = (
            Presentation(spec.template_path) if spec.template_path else Presentation()
        )
        layouts_by_name = {layout.name.lower(): layout for layout in presentation.slide_layouts}
        style = self._resolve_theme_style(spec)

        for slide_spec in spec.slides:
            layout = self._resolve_layout(presentation, layouts_by_name, slide_spec.layout)
            slide = presentation.slides.add_slide(layout)
            self._fill_placeholders(slide, slide_spec.placeholders)
            if style is not None:
                is_hero = slide_spec.layout.lower() in _HERO_LAYOUTS
                self._apply_theme(slide, presentation, style, is_hero)
            for image in slide_spec.images:
                kwargs = {"width": Inches(image.width_inches)} if image.width_inches else {}
                slide.shapes.add_picture(image.source_path, Inches(0.5), Inches(0.5), **kwargs)
            if slide_spec.speaker_notes:
                slide.notes_slide.notes_text_frame.text = slide_spec.speaker_notes

        output_path.parent.mkdir(parents=True, exist_ok=True)
        presentation.save(output_path)

    def _resolve_theme_style(self, spec: PresentationSpec) -> _ThemeStyle | None:
        if spec.theme is None:
            return None
        if spec.template_path is not None:
            logger.warning(
                "artifact.pptx.theme_skipped_for_template",
                extra={"theme": spec.theme.value, "template_path": spec.template_path},
            )
            return None
        return _THEME_STYLES.get(spec.theme)

    def _resolve_layout(self, presentation, layouts_by_name: dict, name: str):
        key = name.lower()
        if key in layouts_by_name:
            return layouts_by_name[key]
        if key in _DEFAULT_LAYOUT_NAMES:
            index = _DEFAULT_LAYOUT_NAMES[key]
            if index < len(presentation.slide_layouts):
                return presentation.slide_layouts[index]
        # Fall back to the first layout rather than raising: an agent
        # requesting an unknown layout name shouldn't lose the whole slide.
        return presentation.slide_layouts[0]

    def _fill_placeholders(self, slide, placeholders: dict[str, str]) -> None:
        all_placeholders = list(slide.placeholders)
        by_idx = {ph.placeholder_format.idx: ph for ph in all_placeholders}
        by_name = {ph.name: ph for ph in all_placeholders}
        title_idx = slide.shapes.title.placeholder_format.idx if slide.shapes.title is not None else None
        filled_idx: set[int] = set()
        remaining = dict(placeholders)

        if "title" in remaining and slide.shapes.title is not None:
            slide.shapes.title.text = remaining.pop("title")
            filled_idx.add(title_idx)

        # Precise targeting: "idx:N" keys, resolved against this layout's
        # actual placeholder indices (see inspect_template()).
        for key in [k for k in remaining if k.startswith("idx:")]:
            try:
                idx = int(key[len("idx:"):])
            except ValueError:
                continue
            ph = by_idx.get(idx)
            if ph is not None and idx not in filled_idx:
                ph.text_frame.text = remaining.pop(key)
                filled_idx.add(idx)

        # Precise targeting: exact placeholder shape name.
        for key in [k for k in remaining if k in by_name]:
            ph = by_name[key]
            idx = ph.placeholder_format.idx
            if idx not in filled_idx:
                ph.text_frame.text = remaining.pop(key)
                filled_idx.add(idx)

        # Positional fallback for whatever's left, into whatever
        # placeholders weren't already filled above -- the original
        # behavior, preserved so old specs (just "title" + one other
        # key) keep working without needing precise targeting.
        leftover_placeholders = [ph for ph in all_placeholders if ph.placeholder_format.idx not in filled_idx]
        for ph, (_, text) in zip(leftover_placeholders, remaining.items()):
            ph.text_frame.text = text

    def _apply_theme(self, slide, presentation, style: _ThemeStyle, is_hero: bool) -> None:
        if is_hero:
            self._apply_hero_background(slide, presentation, style)
            title_hex = style.hero_text_hex
        else:
            self._apply_content_background(slide, presentation, style)
            title_hex = style.content_title_hex

        if slide.shapes.title is not None:
            for paragraph in slide.shapes.title.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor.from_string(title_hex)
                    run.font.name = style.font_name
                    run.font.bold = True
                    run.font.size = Pt(style.title_size_pt)

        if not is_hero:
            for ph in slide.placeholders:
                if ph == slide.shapes.title:
                    continue
                for paragraph in ph.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.color.rgb = RGBColor.from_string(style.body_hex)
                        run.font.name = style.font_name

    def _apply_hero_background(self, slide, presentation, style: _ThemeStyle) -> None:
        background = slide.background
        background.fill.gradient()
        stops = background.fill.gradient_stops
        stops[0].color.rgb = RGBColor.from_string(style.gradient_start_hex)
        stops[0].position = 0.0
        stops[1].color.rgb = RGBColor.from_string(style.gradient_end_hex)
        stops[1].position = 1.0
        background.fill.gradient_angle = style.gradient_angle

        # A large circle mostly off-slide in the bottom-right corner —
        # the "peeking shape" motif common in modern deck design. Only
        # a quarter of it is actually visible.
        diameter = Inches(4)
        decorative = slide.shapes.add_shape(
            MSO_SHAPE.OVAL,
            presentation.slide_width - Inches(2),
            presentation.slide_height - Inches(2),
            diameter,
            diameter,
        )
        decorative.fill.solid()
        decorative.fill.fore_color.rgb = RGBColor.from_string(style.decorative_hex)
        decorative.line.fill.background()
        decorative.shadow.inherit = False

    def _apply_content_background(self, slide, presentation, style: _ThemeStyle) -> None:
        background = slide.background
        background.fill.solid()
        background.fill.fore_color.rgb = RGBColor.from_string(style.content_background_hex)

        # Thin accent bar down the left edge — the one decorative touch
        # on a content slide, kept minimal so it doesn't compete with
        # whatever text/data the slide is actually there to convey.
        accent = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(0.15), presentation.slide_height
        )
        accent.fill.solid()
        accent.fill.fore_color.rgb = RGBColor.from_string(style.accent_hex)
        accent.line.fill.background()
        accent.shadow.inherit = False

    def validate(self, output_path: Path, spec: ArtifactSpec) -> ValidationResult:
        assert isinstance(spec, PresentationSpec)
        errors: list[str] = []
        checks: list[str] = []
        try:
            presentation = Presentation(output_path)
            checks.append("file_opens")
        except Exception as exc:
            return ValidationResult(is_valid=False, errors=(f"unreadable: {exc}",))

        if len(presentation.slides) != len(spec.slides):
            errors.append(
                f"expected {len(spec.slides)} slides, found {len(presentation.slides)}"
            )
        else:
            checks.append("slide_count")

        return ValidationResult(is_valid=not errors, checks_passed=tuple(checks), errors=tuple(errors))
